#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
TS <-> Matrix notifier
- Owns the asyncio loop (no bot.run()) to allow watchdog timeouts and clean shutdowns
- Adds TS3Safe wrapper that auto-reconnects on closed/broken sockets and retries once
- Ensures aiohttp sessions are always closed (removes "Unclosed client session" spam)
- Adds jittered backoff to avoid restart thrash
- Defers TS3 event subscription until an asyncio loop exists (fixes dropped events)
- Re-subscribes TS3 events on reconnect
- Loads praise/apology messages from a JSON file (BOT_MESSAGES_FILE, default: bot_messages.json)
- Adds a 10s presence reconciliation heartbeat to announce missed joins/leaves
- Notify callback accepts positional or keyword events; event sends are fire-and-forget
- TS3 heartbeat reduced to 12s to detect/recover dropped sockets faster
- Force rebind of TS3 notifies on reconnect; cleanly stop/quit old TS3 connections
- SyncResponse-per-minute counter + watchdog that restarts when sync rate = 0
- Log sync rate every 60 seconds
- Watchdog flag to enable a time-based watchdog (disabled by default)
- Windows/Linux-aware session & data directories

NEW:
- Graceful handling of an unavailable Matrix homeserver:
  * Preflight liveness probe (/_matrix/client/versions) with exponential backoff + jitter
  * Transient error classification during sync/login → clean shutdown + progressive backoff
  * No noisy tracebacks for expected outage modes
  * Fixes 'room.roomid' AttributeError
"""

print("tsmatrix_notify.py starting…")

import os
import json
import time
import logging
import argparse
import sys
import socket
import random
import inspect
import traceback
import asyncio
import platform
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from ts3API.TS3Connection import TS3Connection, TS3QueryException
import ts3API.Events as Events
from ts3API.utilities import TS3ConnectionClosedException
import simplematrixbotlib as botlib
from nio import SyncResponse
import aiohttp

# pylint: disable=W0718

import threading

# ──────────────────────────────────────────────────────────────────────────────
# Logging (with a custom SYNC level below DEBUG)
# ──────────────────────────────────────────────────────────────────────────────
SYNC_LEVEL_NUM = 5
logging.addLevelName(SYNC_LEVEL_NUM, "SYNC")
def _sync(self, message, *args, **kwargs):
    if self.isEnabledFor(SYNC_LEVEL_NUM):
        self._log(SYNC_LEVEL_NUM, message, args, **kwargs)  # pylint: disable=W0212
logging.Logger.sync = _sync

def setup_logger(debug: bool, trace: bool):
    if trace:
        lvl = SYNC_LEVEL_NUM
    elif debug:
        lvl = logging.DEBUG
    else:
        lvl = logging.INFO
    log = logging.getLogger("TSMatrixNotify")
    log.setLevel(lvl)
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)5s %(message)s"))
    log.addHandler(h)
    if debug or trace:
        logging.getLogger("nio").setLevel(logging.DEBUG)
        logging.getLogger("nio.client").setLevel(logging.DEBUG)
        logging.getLogger("aiohttp").setLevel(logging.WARNING)
    return log

# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser("TSMatrixNotify bridge")
    p.add_argument('-d', '--debug', action='store_true', help='Enable debug logging')
    p.add_argument('-t', '--trace', action='store_true', help='Enable full SyncResponse tracing')
    p.add_argument('--no-startup', action='store_true', help="Don't send the startup announcement to Matrix")
    p.add_argument('--watchdog', action='store_true', default=False,
                   help='Enable time-based watchdog (timeout from WATCHDOG_TIMEOUT env; default 1800s)')
    return p.parse_args()

# ──────────────────────────────────────────────────────────────────────────────
# Message catalog loader (JSON)
# ──────────────────────────────────────────────────────────────────────────────
def load_message_catalog(path, log):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        msgs = data.get("messages", [])
        apols = data.get("apologies", [])
        if not isinstance(msgs, list) or not isinstance(apols, list):
            raise ValueError("messages/apologies must be lists")
        if not msgs:
            log.warning("Message catalog has no 'messages'; using a tiny fallback.")
            msgs = ["Thanks!"]
        if not apols:
            log.warning("Message catalog has no 'apologies'; using a tiny fallback.")
            apols = ["Sorry!"]
        log.info("Loaded message catalog: %d messages, %d apologies", len(msgs), len(apols))
        return msgs, apols
    except Exception as e:
        log.error("Failed to load message catalog %r: %s", path, e)
        return ["Thanks!"], ["Sorry!"]

# ──────────────────────────────────────────────────────────────────────────────
# TS3 Connection helpers
# ──────────────────────────────────────────────────────────────────────────────
def connect_ts3(host, port, user, pw, vsid, log):
    """Connect, login, select vserver, log version."""
    while True:
        try:
            log.debug("Testing TCP %s:%d …", host, port)
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
        except Exception as e:
            log.error("Network unreachable: %s", e)
            time.sleep(5)
            continue

        try:
            conn = TS3Connection(host, port)
            log.debug("Logging in as %r", user)
            conn.login(user, pw)
            log.info("TS3 login successful")

            ver = conn.version()
            if isinstance(ver, dict):
                ver_str = f"{ver.get('version','<unknown>')} build={ver.get('build','?')} on {ver.get('platform','?')}"
            else:
                parsed = getattr(ver, "parsed", None)
                ver_str = parsed[0]['version'] if parsed else "<unknown>"
            log.info("ServerQuery version: %s", ver_str)

            conn.use(sid=vsid)
            log.info("Using virtual server %d", vsid)
            return conn

        except TS3QueryException as e:
            msg = str(e).lower()
            if "insufficient client permissions" in msg:
                log.error("Insufficient TS3 permissions; aborting")
                sys.exit(1)
            log.error("TS3QueryException: %s", e)
            time.sleep(5)

        except Exception as e:
            log.error("TS3 connection error: %s", e)
            time.sleep(5)

# ──────────────────────────────────────────────────────────────────────────────
# TS3Safe wrapper with *clean* reconnect + forced notify rebind
# ──────────────────────────────────────────────────────────────────────────────
def _join_keepalive_if_present(conn, log, timeout=2.0):
    """Best-effort join of the library's keepalive thread (if exposed)."""
    try:
        t = getattr(conn, "_keepalive_thread", None)
        # Only join if it's actually a threading.Thread
        if isinstance(t, threading.Thread) and t.is_alive():
            t.join(timeout)
    except Exception:
        log.debug("keepalive join best-effort failed", exc_info=True)

class TS3Safe:
    """
    Wraps a TS3Connection with:
    - auto-reconnect on TS3ConnectionClosedException / OSError
    - single retry of the current command
    - post_connect to re-register events/keepalive on new connection
    - notify_binder to (re)bind TS3 notifies after reconnect
    - CLEAN shutdown of the *old* connection to avoid WinError 10038
    """
    def __init__(self, connect_factory, post_connect, log, notify_binder=None):
        self._connect_factory = connect_factory   # () -> TS3Connection
        self._post_connect    = post_connect      # (TS3Connection) -> None
        self._notify_binder   = notify_binder     # (TS3Connection, log) -> None
        self._log             = log
        self._conn            = connect_factory()
        # ensure fresh notify state on a brand-new connection
        setattr(self._conn, "_notifies_bound", False)
        if self._post_connect:
            self._post_connect(self._conn)

    @property
    def conn(self):
        return self._conn

    def _clean_close(self, conn: TS3Connection):
        if not conn:
            return
        try:
            try:
                conn.stop_keepalive_loop()
            except Exception:
                pass
            _join_keepalive_if_present(conn, self._log)
            try:
                conn.quit()
            except Exception:
                pass
        except Exception:
            self._log.exception("TS3Safe: error while closing old TS3 connection")

    def _reconnect(self):
        self._log.warning("TS3 reconnecting…")

        # Cleanly stop the *old* connection (prevents WinError 10038 from keepalive thread)
        old = self._conn
        self._clean_close(old)

        # Create a brand-new connection
        new_conn = self._connect_factory()

        # Force notify rebind for this *new* connection
        setattr(new_conn, "_notifies_bound", False)

        self._conn = new_conn
        self._log.info("TS3 reconnected")

        # Post-connect hooks + (re)bind notifies
        try:
            if self._post_connect:
                self._post_connect(self._conn)
            if self._notify_binder:
                self._notify_binder(self._conn, self._log)
        except Exception:
            self._log.exception("TS3 post-connect/notify-binder hook failed")

    def call(self, fn, *args, **kwargs):
        try:
            return fn(self._conn, *args, **kwargs)
        except (TS3ConnectionClosedException, OSError) as e:
            self._log.warning("TS3 call failed (%r); retrying after reconnect", e)
            self._reconnect()
            return fn(self._conn, *args, **kwargs)

# Small adaptors
def ts3_version_safe(ts3safe: TS3Safe):
    def _version(conn: TS3Connection):
        ver = conn.version()
        if isinstance(ver, dict):
            return ver.get("version", "<unknown>")
        parsed = getattr(ver, "parsed", None)
        return parsed[0]["version"] if parsed else "<unknown>"
    return ts3safe.call(_version)

def ts3_clientlist_safe(ts3safe: TS3Safe):
    return ts3safe.call(lambda c: c.clientlist())

def ts3_clientinfo_safe(ts3safe: TS3Safe, clid):
    return ts3safe.call(lambda c: c.clientinfo(clid))

# ──────────────────────────────────────────────────────────────────────────────
# Matrix helpers
# ──────────────────────────────────────────────────────────────────────────────
def connect_matrix(creds, log):
    while True:
        try:
            if not creds.homeserver or not str(creds.homeserver).strip().lower().startswith(("http://", "https://")):
                raise ValueError("Invalid Homeserver")
            log.debug("Initializing Matrix bot for %s", creds.username)
            bot = botlib.Bot(creds)
            log.info("Matrix bot created for %s", creds.username)
            return bot
        except ValueError as e:
            # Configuration error—log clearly, retry with modest delay (operator might fix env)
            log.error("Matrix init failed: %s", e)
            time.sleep(5)
        except Exception as e:
            # Transient init error
            log.error("Matrix init failed: %s", e)
            time.sleep(5)

class MatrixHelper:
    """Helper for sending Matrix messages with unified logging."""
    def __init__(self, bot, loop, log):
        self.bot  = bot
        self.loop = loop
        self.log  = log

    def send(self, room_id: str, text: str, clid=None):
        coro = self.bot.api.send_text_message(room_id, text)
        fut  = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(lambda f: log_future_result(self.log, clid)(f))

def log_future_result(log, clid=None):
    def _on_done(fut):
        try:
            fut.result()
            log.info("Matrix message sent%s", f" for clid={clid}" if clid else "")
        except Exception as exc:
            log.error("Failed to send Matrix message%s: %s", f" for clid={clid}" if clid else "", exc)
    return _on_done

class RestartBot(Exception):
    """Internal signal to restart the bot gracefully."""
    pass

async def shutdown_bot(bot, log):
    """Gracefully close the Matrix HTTP session."""
    try:
        client = getattr(bot, "async_client", None)
        if client:
            log.debug("Closing Matrix aiohttp session…")
            await client.close()
            log.info("Matrix session closed.")
    except Exception:
        log.exception("Error during bot shutdown")

# ──────────────────────────────────────────────────────────────────────────────
# OS-aware session & data directories
# ──────────────────────────────────────────────────────────────────────────────
def choose_paths(log: logging.Logger):
    """
    Returns (session_dir: str, session_file: str, data_dir: Path, stats_path: Path)
    Uses env overrides if present:
      MATRIX_SESSION_DIR: directory for session file
      MATRIX_SESSION_FILE: full path to session file (overrides name)
      TSMATRIX_DATA_DIR: directory for app data (stats, etc.)
    """
    is_windows = platform.system().lower().startswith("win")

    # Default roots per OS
    if is_windows:
        local_app = os.getenv("LOCALAPPDATA") or tempfile.gettempdir()
        state_root = Path(local_app) / "TSMatrixNotify"
        data_root  = Path(local_app) / "TSMatrixNotify"
        session_dir_default = state_root / "session"
        data_dir_default    = data_root / "data"
    else:
        home = Path.home()
        state_home = Path(os.getenv("XDG_STATE_HOME", home / ".local" / "state"))
        data_home  = Path(os.getenv("XDG_DATA_HOME",  home / ".local" / "share"))
        session_dir_default = state_home / "tsmatrix_notify" / "session"
        data_dir_default    = data_home  / "tsmatrix_notify"

    # Allow overrides
    session_dir = Path(os.getenv("MATRIX_SESSION_DIR", str(session_dir_default)))
    data_dir    = Path(os.getenv("TSMATRIX_DATA_DIR",  str(data_dir_default)))

    # Ensure directories exist, with temp fallback on failure
    def ensure_dir(p: Path, label: str) -> Path:
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception as e:
            log.warning("Failed to create %s dir %s (%r); falling back to temp", label, p, e)
            fallback = Path(tempfile.gettempdir()) / "tsmatrix_notify" / label
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    session_dir = ensure_dir(session_dir, "session")
    data_dir    = ensure_dir(data_dir, "data")

    # Session file (allow full override)
    session_file = os.getenv("MATRIX_SESSION_FILE")
    if session_file:
        session_file = str(session_file)
    else:
        session_file = str(session_dir / "matrix_session.json")

    stats_path = data_dir / "bot_reviews_stats.json"
    return str(session_dir), session_file, data_dir, stats_path

# ──────────────────────────────────────────────────────────────────────────────
# Simple persistence for !goodbot / !badbot counters (atomic, absolute path)
# ──────────────────────────────────────────────────────────────────────────────
def make_stats_helpers(stats_path: Path, log: logging.Logger):
    def load_stats():
        try:
            if stats_path.exists():
                with stats_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            log.warning("Failed to load stats from %s: %r (resetting).", stats_path, e)
        return {"good": 0, "bad": 0}

    def save_stats(stats):
        try:
            # atomic write: temp file in same dir, fsync, replace
            with tempfile.NamedTemporaryFile("w",
                                             encoding="utf-8",
                                             dir=str(stats_path.parent),
                                             delete=False) as tmp:
                json.dump(stats, tmp)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = tmp.name
            os.replace(tmp_path, stats_path)
        except Exception as e:
            log.error("Failed to save stats to %s: %r", stats_path, e)

    def get_summary_message(stats):
        good = int(stats.get("good", 0))
        bad  = int(stats.get("bad", 0))
        total = good + bad
        return f"Review summary — 👍: {good}, 👎: {bad} total reviews: {total}"

    return load_stats, save_stats, get_summary_message

# ──────────────────────────────────────────────────────────────────────────────
# Optional TS3 manager (not used directly; kept for completeness)
# ──────────────────────────────────────────────────────────────────────────────
class TS3Manager:
    def __init__(self, host, port, user, pw, vsid, log):
        self.host, self.port, self.user, self.pw, self.vsid = host, port, user, pw, vsid
        self.log = log
        self.conn = None
        self._keepalive_started = False
        self._event_cb = None
        self._lock = threading.Lock()

    def _connect_once(self):
        conn = TS3Connection(self.host, self.port)
        conn.login(self.user, self.pw)
        conn.use(sid=self.vsid)
        self.log.info("TS3 connected & vserver selected")
        return conn

    def connect(self):
        with self._lock:
            while True:
                try:
                    self.conn = self._connect_once()
                    if self._event_cb:
                        self._register_events_locked(self._event_cb)
                    if not self._keepalive_started:
                        self.conn.start_keepalive_loop()
                        self._keepalive_started = True
                    return
                except Exception as e:
                    self.log.error("TS3 connect failed: %s", e)
                    time.sleep(5)

    def _register_events_locked(self, cb):
        try:
            self.conn.register_for_server_events(cb)
            self.conn.register_for_channel_events(0, cb)
            self.log.info("TS3 events subscribed")
        except Exception:
            self.log.exception("TS3 event registration failed")
            raise

    def register_events(self, cb):
        with self._lock:
            self._event_cb = cb
            if self.conn:
                self._register_events_locked(cb)

    def call(self, fn, *, retries=1):
        with self._lock:
            try:
                return fn(self.conn)
            except (TS3ConnectionClosedException, OSError) as e:
                self.log.warning("TS3 call failed (socket closed): %s", e)
                if retries <= 0:
                    raise
                self.log.info("Reconnecting TS3…")
                try:
                    try:
                        self.conn.stop_keepalive_loop()
                    except Exception:
                        pass
                    try:
                        self.conn.quit()
                    except Exception:
                        pass
                finally:
                    self._keepalive_started = False
                self.connect()
                return self.call(fn, retries=retries - 1)

    def close(self):
        with self._lock:
            try:
                if self.conn:
                    try:
                        self.conn.stop_keepalive_loop()
                    except Exception:
                        pass
                    try:
                        self.conn.quit()
                    except Exception:
                        pass
            finally:
                self.conn = None
                self._keepalive_started = False

# ──────────────────────────────────────────────────────────────────────────────
# Backoff helpers for Matrix availability
# ──────────────────────────────────────────────────────────────────────────────
async def probe_homeserver(hs: str, log: logging.Logger, timeout_s: int = 6) -> bool:
    """Return True if GET /_matrix/client/versions succeeds."""
    if not hs:
        log.error("Matrix homeserver not configured")
        return False
    url = hs.rstrip("/") + "/_matrix/client/versions"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=timeout_s) as r:
                _ = await r.read()
                ok = 200 <= r.status < 300
                if ok:
                    log.info("versions probe: %s -> %s", url, r.status)
                else:
                    log.warning("versions probe non-OK: %s -> %s", url, r.status)
                return ok
    except Exception as e:
        log.warning("versions probe failed: %r", e)
        return False

async def await_homeserver_ready(hs: str, log: logging.Logger,
                                 min_backoff: float = 5.0,
                                 max_backoff: float = 600.0) -> None:
    """Block until homeserver answers versions; exponential backoff with jitter."""
    delay = min_backoff
    while True:
        ok = await probe_homeserver(hs, log)
        if ok:
            return
        jitter = random.uniform(0, delay / 2)
        wait = min(max_backoff, delay + jitter)
        log.info("Matrix unavailable; retrying in %.1fs (backoff=%ss)", wait, int(delay))
        await asyncio.sleep(wait)
        delay = min(max_backoff, delay * 2 or min_backoff)

def is_transient_matrix_error(exc: Exception) -> bool:
    """Classify common Matrix connectivity/login/sync failures as transient."""
    from aiohttp.client_exceptions import ClientConnectorError
    transient_types = (asyncio.TimeoutError, TimeoutError, ClientConnectorError, ConnectionError)
    if isinstance(exc, transient_types):
        return True
    # simplematrixbotlib raises ValueError("Invalid Homeserver") when URL is bad or offline during init/sync
    if isinstance(exc, ValueError) and "Invalid Homeserver" in str(exc):
        return True
    # nio sometimes forwards M_UNKNOWN for server issues
    if isinstance(exc, Exception) and "M_UNKNOWN" in str(exc):
        return True
    return False

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main():
    args = parse_args()
    log  = setup_logger(args.debug, args.trace)
    load_dotenv()

    # Message catalog
    msg_file = os.getenv("BOT_MESSAGES_FILE", "bot_messages.json")
    messages, apologies = load_message_catalog(msg_file, log)

    # OS-aware dirs (session + data)
    session_dir, session_file, data_dir, stats_path = choose_paths(log)
    log.debug("Session dir: %s", session_dir)
    log.debug("Data dir:    %s", data_dir)
    log.debug("Session file: %s", session_file)

    # Stats helpers bound to absolute stats_path
    load_stats, save_stats, get_summary_message = make_stats_helpers(stats_path, log)

    # TS3 config
    ts3_host  = os.getenv("TS3_HOST", "127.0.0.1")
    ts3_port  = int(os.getenv("TS3_PORT", "10011"))
    ts3_user  = os.getenv("TS3_USER")
    ts3_pw    = os.getenv("TS3_PASSWORD")
    vsid      = int(os.getenv("TS3_VSERVER_ID", "1"))

    # Matrix config (force session file path)
    creds = botlib.Creds(
        homeserver   = os.getenv("MATRIX_HOMESERVER"),
        username     = os.getenv("MATRIX_USER_ID"),
        access_token = os.getenv("MATRIX_ACCESS_TOKEN"),
        session_stored_file=session_file,
    )
    room_id = os.getenv("MATRIX_ROOM_ID")

    # Shared state
    join_times   = {}  # clid -> join timestamp
    client_names = {}  # clid -> nickname

    # Matrix readiness gate for background tasks that send messages
    api_ready = asyncio.Event()

    # These will be set when the loop starts
    global running_loop
    running_loop = None

    # SyncResponse counting (per 60s)
    sync_count_60s = 0

    # Debug helpers
    def _dns_debug(tag: str, homeserver: str):
        try:
            host = homeserver.split("://", 1)[-1].split("/", 1)[0].split("@")[-1]
            host = host.split(":", 1)[0]
            infos = socket.getaddrinfo(host, None)
            addrs = sorted({ai[4][0] for ai in infos})
            log.debug("%s DNS: %s -> %s", tag, host, ", ".join(addrs) or "<none>")
        except Exception as e:
            log.warning("%s DNS resolution failed: %r", tag, e)

    async def _matrix_http_versions(hs: str, timeout_s: int = 6):
        """GET /_matrix/client/versions to check homeserver liveness, bypassing nio."""
        url = hs.rstrip("/") + "/_matrix/client/versions"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=timeout_s) as r:
                    txt = await r.text()
                    log.info("versions probe: %s -> %s len=%d", url, r.status, len(txt))
        except Exception as e:
            log.warning("versions probe failed: %r", e)

    async def _matrix_whoami(bot, tag: str, timeout_s: int = 8):
        try:
            ac = getattr(bot, "async_client", None)
            if not ac:
                log.debug("%s whoami: no async_client yet", tag)
                return
            res = await asyncio.wait_for(ac.whoami(), timeout=timeout_s)
            log.info("%s whoami: OK user_id=%s", tag, getattr(res, "user_id", None))
        except asyncio.TimeoutError:
            log.warning("%s whoami: TIMEOUT after %ss", tag, timeout_s)
        except Exception as e:
            log.warning("%s whoami: %r", tag, e)

    def _dump_debug_state(loop: asyncio.AbstractEventLoop, bot, note: str, bot_task: asyncio.Task | None):
        try:
            log.info("=== DEBUG SNAPSHOT: %s ===", note)
            # Threads
            thr = threading.enumerate()
            log.info("Threads (%d): %s", len(thr), ", ".join(t.name for t in thr))
            # Loop
            try:
                sel = type(loop._selector).__name__ if hasattr(loop, "_selector") else "<unknown>"
            except Exception:
                sel = "<unknown>"
            log.info("Loop: closed=%s debug=%s selector=%s", loop.is_closed(), loop.get_debug(), sel)
            # Current thread stack (top few frames)
            try:
                stack_str = "".join(traceback.format_stack(limit=8))
                log.debug("Current thread stack (top 8 frames):\n%s", stack_str)
            except Exception:
                pass
            # Bot task state
            if bot_task is not None:
                state = "done" if bot_task.done() else "pending"
                log.info("bot_task: %s cancelled=%s done=%s", state, bot_task.cancelled(), bot_task.done())
                if bot_task.done() and not bot_task.cancelled():
                    try:
                        log.info("bot_task result: %r", bot_task.result())
                    except Exception as e:
                        log.info("bot_task exception: %r", e)
                        try:
                            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__, limit=8))
                            log.debug("bot_task exception traceback:\n%s", tb)
                        except Exception:
                            pass
            # Tasks snapshot
            tasks = [t for t in asyncio.all_tasks(loop)]
            log.info("Tasks (%d):", len(tasks))
            for t in tasks[:25]:
                st = "done" if t.done() else "pending"
                nm = getattr(t, "get_name", lambda: "<unnamed>")()
                log.info(" - %s state=%s cancelled=%s", nm, st, getattr(t, "cancelled", lambda: False)())
                try:
                    frames = t.get_stack(limit=1)
                    if frames:
                        f = frames[0]
                        info = inspect.getframeinfo(f)
                        log.info("   at %s:%d in %s", info.filename, info.lineno, info.function)
                except Exception:
                    pass
            if len(tasks) > 25:
                log.info(" … %d more tasks omitted", len(tasks) - 25)
            # TS3 / Matrix basics
            try:
                v = ts3_version_safe(ts3safe)
                log.info("TS3: version=%s", v)
            except Exception as e:
                log.warning("TS3: version probe failed: %r", e)
            try:
                ac = getattr(bot, "async_client", None)
                base_url = getattr(ac, "homeserver", None)
                log.info("Matrix: homeserver=%s token_present=%s", base_url, bool(getattr(ac, "access_token", None)))
            except Exception:
                pass
            log.info("=== END SNAPSHOT ===")
        except Exception:
            log.exception("debug snapshot failed")

    # ── TS3 notify binder (reusable) ──────────────────────────────────────────
    def bind_ts3_notifies(conn: TS3Connection, log):
        """Register TS3 notify callbacks exactly once per connection."""
        if getattr(conn, "_notifies_bound", False):
            log.debug("TS3 notify handlers already bound for this connection; skipping")
            return

        def on_ts3_event(*args, **kwargs):
            ev = kwargs.get("event")
            if ev is None and args:
                ev = args[-1]
            if ev is None:
                log.warning("TS3 notify callback invoked without an event: args=%r kwargs=%r", args, kwargs)
                return

            log.debug("TS3 raw event: %s %r", type(ev).__name__, getattr(ev, "_data", {}))
            if not running_loop:
                log.error("No running_loop; dropping TS3 event %s", type(ev).__name__)
                return
            fut = asyncio.run_coroutine_threadsafe(handle_ts3_event(ev), running_loop)
            def _done(f):
                try:
                    exc = f.exception()
                    if exc:
                        log.exception("handle_ts3_event failed: %r", exc)
                except asyncio.CancelledError:
                    pass
            fut.add_done_callback(_done)

        conn.register_for_server_events(on_ts3_event)
        conn.register_for_channel_events(0, on_ts3_event)
        conn._notifies_bound = True  # mark as subscribed
        log.info("TS3 events subscribed (loop OK)")

    # ── TS3 post-connect hook: keepalive + seed users + subscribe events ──────
    def _post_connect_register(conn: TS3Connection):
        # Always force a rebind decision on a fresh connection
        setattr(conn, "_notifies_bound", getattr(conn, "_notifies_bound", False))
        bind_ts3_notifies(conn, log)

        # Start keepalive (only once per connection)
        try:
            conn.start_keepalive_loop()
        except Exception:
            pass  # some libs ignore duplicate starts

        # Seed clients
        try:
            for c in conn.clientlist():
                if c.get("client_type") != "0":
                    continue
                clid = c["clid"]
                client_names[clid] = c.get("client_nickname", "?")
                join_times[clid]   = time.time()
        except Exception:
            log.exception("Unable to seed initial TS3 client list")

    # Real connect factory
    def _ts3_connect_factory():
        return connect_ts3(ts3_host, ts3_port, ts3_user, ts3_pw, vsid, log)

    # ──────────────────────────────────────────────────────────────────────────
    # Async handlers (Matrix commands)
    # ──────────────────────────────────────────────────────────────────────────
    async def handle_ping(room_id: str, message, bot, log):
        sent_ts = message.source.get("origin_server_ts", 0)
        now_ms  = int(time.time() * 1000)
        latency = max(0, now_ms - int(sent_ts))
        log.info("Responding to !ping: %d ms", latency)
        await bot.api.send_text_message(room_id, f"PONG! Latency: {latency} ms")

    async def handle_what(room_id: str, message, bot, log):
        log.info("Responding to !what")
        await bot.api.send_text_message(room_id, f"When?")

    async def handle_where(room_id: str, message, bot, log):
        log.info("Responding to !where")
        await bot.api.send_text_message(room_id, f"and Why do I delight in??")

    async def handle_ts3health(room_id: str, bot, log):
        try:
            ver_txt = ts3_version_safe(ts3safe)
            await bot.api.send_text_message(room_id, f"✅ TS3 reachable. Version: {ver_txt}")
            log.info("Responded to !ts3health: %s", ver_txt)
        except Exception as e:
            log.error("!ts3health failed: %r", e)
            await bot.api.send_text_message(room_id, f"❌ TS3 health check failed: {e!r}")

    async def handle_goodbot(room_id: str, bot):
        stats = load_stats()
        stats["good"] = int(stats.get("good", 0)) + 1
        try:
            save_stats(stats)
        except Exception:
            pass
        await bot.api.send_text_message(room_id, random.choice(messages))
        await bot.api.send_text_message(room_id, get_summary_message(stats))

    async def handle_badbot(room_id: str, bot, message, log):
        stats = load_stats()
        stats["bad"] = int(stats.get("bad", 0)) + 1
        try:
            save_stats(stats)
        except Exception:
            pass
        apology = random.choice(apologies)
        await bot.api.send_text_message(room_id, apology)
        await bot.api.send_text_message(room_id, get_summary_message(stats))
        log.debug("!badbot invoked by %s, apology: %s", message.sender, apology)

    def build_who_body():
        """Return (body, count) matching the !who template."""
        clist = ts3_clientlist_safe(ts3safe)
        now   = time.time()
        lines = []
        for c in clist:
            if c.get("client_type") != "0":
                continue
            clid = c["clid"]
            try:
                info = ts3_clientinfo_safe(ts3safe, clid)
            except Exception as err:
                log.error("clientinfo failed for clid=%s: %s", clid, err)
                continue
            nick    = info.get("client_nickname", "?")
            started = join_times.get(clid)
            if started:
                delta = int(now - started)
                hrs, rem   = divmod(delta, 3600)
                mins, secs = divmod(rem, 60)
                up = f"{hrs}h{mins}m{secs}s"
            else:
                up = "unknown"
            parts = []
            if info.get("client_away") == "1":
                msg = info.get("client_away_message", "").strip()
                parts.append(f"away{': '+msg if msg else ''}")
            if info.get("client_input_muted") == "1":
                parts.append("mic muted")
            if info.get("client_output_muted") == "1":
                parts.append("spk muted")
            status = f" [{' / '.join(parts)}]" if parts else ""
            lines.append(f"- {nick} online: {up}{status}")
        if not lines:
            return "👥 No clients online.", 0
        return "👥 Online TS3 users:\n" + "\n".join(lines), len(lines)

    async def handle_ts3online(room_id: str, log, bot):
        log.debug("Handling ts3online: fetching client list")
        try:
            body, count = build_who_body()
            if count == 0:
                log.info("ts3online: no clients found")
            else:
                log.info("ts3online: %d clients listed", count)
            await bot.api.send_text_message(room_id, body)
        except Exception:
            log.exception("ts3online: error fetching clients")
            await bot.api.send_text_message(room_id, f"❌ Could not fetch online users: {sys.exc_info()[1]!r}")

    async def handle_restart(room_id: str, message, bot, log):
        log.info("Restart requested by %s", message.sender)
        await bot.api.send_text_message(room_id, "🔄 Restarting bot…")
        raise RestartBot()

    async def handle_help(room_id: str, bot, log):
        help_text = (
            "Available commands:\n"
            "- !ping, !p        – check bot↔️Matrix latency\n"
            "- !goodbot, !gb    – praise the bot\n"
            "- !badbot, !bb     – reprimand the bot\n"
            "- !ts3health, !th  – TS3 health & version\n"
            "- !ts3online, !online, !who, !list  – list current TS3 clients\n"
            "- !restart, !rs    – manually restart the bot\n"
            "- !debug, !d       – run all commands in sequence for testing\n"
            "- !help, !h, !man  – show this message\n"
        )
        log.debug("Responding to !help")
        await bot.api.send_text_message(room_id, help_text)

    async def handle_debug(room_id: str, message, bot, log):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ts3_target = f"{ts3_host}:{ts3_port} (vsid={vsid})"
        try:
            inst = ts3safe.call(lambda c: c.hostinfo())
            inst_secs = int(inst.get("instance_uptime", 0))
            d, r = divmod(inst_secs, 86400)
            h, r = divmod(r, 3600)
            m, s = divmod(r, 60)
            inst_uptime = f"{d}d{h}h{m}m{s}s"
        except Exception:
            inst_uptime = "<error>"

        try:
            vs = ts3safe.call(lambda c: c.serverinfo())
            vs_secs = int(vs.get("virtualserver_uptime", 0))
            d, r = divmod(vs_secs, 86400)
            h, r = divmod(r, 3600)
            m, s = divmod(r, 60)
            vs_uptime = f"{d}d{h}h{m}m{s}s"
        except Exception:
            vs_uptime = "<error>"

        try:
            who = await bot.async_client.whoami()
            mx_user_id = getattr(who, "user_id", creds.username)
        except Exception:
            mx_user_id = creds.username

        summary = (
            "🛠️ **DEBUG SUMMARY**\n"
            f"- UTC now:                   {now}\n"
            f"- TS3 target:                {ts3_target}\n"
            f"- TS3 instance uptime:       {inst_uptime}\n"
            f"- TS3 virtualserver uptime:  {vs_uptime}\n"
            f"- Matrix homeserver:         {creds.homeserver}\n"
            f"- Matrix user_id (whoami):   {mx_user_id}\n"
            f"- Matrix room:               {room_id}\n"
        )
        await bot.api.send_text_message(room_id, summary)
        log.info("Posted debug summary to Matrix")

        sequence = [
            ("ping",      handle_ping,      (room_id, message, bot, log)),
            ("ts3health", handle_ts3health, (room_id, bot, log)),
            ("goodbot",   handle_goodbot,   (room_id, bot)),
            ("ts3online", handle_ts3online, (room_id, log, bot)),
            ("help",      handle_help,      (room_id, bot, log)),
        ]
        for name, fn, args_ in sequence:
            log.info("Debug run: !%s", name)
            await bot.api.send_text_message(room_id, f"▶️ Debug: testing !{name}")
            await fn(*args_)
            await asyncio.sleep(3)

        await bot.api.send_text_message(room_id, "✅ Debug run complete.")
        log.info("Finished debug run of all commands")

    # ── TS3 event handler (async)
    async def handle_ts3_event(ev):
        data = getattr(ev, "_data", {})
        log.debug("TS3 event: %s %r", type(ev).__name__, data)
        text = None

        if isinstance(ev, Events.ClientEnteredEvent):
            clid = data["clid"]
            nick = data.get("client_nickname", "?")
            client_names[clid] = nick
            join_times[clid]   = time.time()
            text = f"▶️ {nick} joined TS3."
            log.info("ClientEntered: %s (%s)", nick, clid)

        elif isinstance(ev, Events.ClientMovedSelfEvent):
            clid, new = data["clid"], data["ctid"]
            nick = client_names.get(clid, "?")
            text = f"🔀 {nick} moved to channel {new}"
            log.info("ClientMovedSelf: %s → %s", nick, new)

        elif isinstance(ev, Events.ClientMovedEvent):
            clid, new = data["clid"], data["ctid"]
            nick    = client_names.get(clid, "?")
            invoker = data.get("invokername", "<unknown>")
            text    = f"🔀 {nick} was moved to {new} by {invoker}"
            log.info("ClientMoved: %s → %s by %s", nick, new, invoker)

        elif isinstance(ev, Events.ClientLeftEvent):
            clid = data["clid"]
            nick = client_names.pop(clid, "<unknown>")
            join_times.pop(clid, None)
            text = f"◀️ {nick} left TS3."
            log.info("ClientLeft: %s (%s)", nick, clid)

        elif isinstance(ev, Events.ClientKickFromChannelEvent):
            clid, reason = data["clid"], data.get("reasonmsg", "")
            nick = client_names.get(clid, "?")
            text = f"⚠️ {nick} kicked from channel. Reason: {reason}"
            client_names.pop(clid, None)
            join_times.pop(clid, None)
            log.info("KickedFromChannel: %s (%s)", nick, clid)

        elif isinstance(ev, Events.ClientKickFromServerEvent):
            clid, reason = data["clid"], data.get("reasonmsg", "")
            nick = client_names.get(clid, "?")
            text = f"⚠️ {nick} kicked from server. Reason: {reason}"
            client_names.pop(clid, None)
            join_times.pop(clid, None)
            log.info("KickedFromServer: %s (%s)", nick, clid)

        elif isinstance(ev, Events.ClientBanEvent) or type(ev).__name__.lower().startswith("ban"):
            cldbid, reason = data.get("cldbid", ""), data.get("reasonmsg", "")
            nick = client_names.get(cldbid, "<unknown>")
            text = f"⛔️ {nick} banned. Reason: {reason}"
            client_names.pop(cldbid, None)
            join_times.pop(cldbid, None)
            log.info("BanEvent: %s (%s)", nick, cldbid)

        else:
            log.debug("Unhandled TS3 event: %s", type(ev).__name__)

        if text:
            log.debug("Sending Matrix notification: %s", text)
            try:
                helper.send(room_id, text, clid=data.get("clid"))
            except Exception:
                log.exception("Error scheduling TS3 event notification")

    # ── Presence reconciliation heartbeat
    async def reconcile_ts3_presence(bot, room_id, log):
        client = getattr(bot, "async_client", None)
        if not client or not getattr(client, "access_token", None):
            log.debug("reconcile_ts3_presence: Matrix client not ready; skipping")
            return

        known = set(client_names.keys())
        try:
            clist = ts3_clientlist_safe(ts3safe)
            current = set()
            now = time.time()
            for c in clist:
                if c.get("client_type") != "0":
                    continue
                clid = c["clid"]
                current.add(clid)
                if clid not in known:
                    client_names[clid] = c.get("client_nickname", "?")
                    join_times[clid] = now
                    try:
                        await bot.api.send_text_message(room_id, f"▶️ {client_names[clid]} joined TS3.")
                    except Exception:
                        log.exception("Failed to send late join announce")
            for clid in list(known - current):
                nick = client_names.pop(clid, "<unknown>")
                join_times.pop(clid, None)
                try:
                    await bot.api.send_text_message(room_id, f"◀️ {nick} left TS3.")
                except Exception:
                    log.exception("Failed to send late leave announce")
        except Exception:
            log.exception("reconcile_ts3_presence failed")

    # ─────────────────────────────────────────────────────────────────────────────
    # Main restart loop: own the event loop; watchdog; clean shutdown; backoff
    # ─────────────────────────────────────────────────────────────────────────────
    backoff = 5  # seconds, will jitter and cap (outer loop)

    while True:
        main_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(main_loop)
        running_loop = main_loop

        # readiness gate per iteration
        if api_ready.is_set():
            api_ready.clear()

        # per-iteration singletons/guards
        heartbeat_task = None
        startup_once = asyncio.Event()
        class FirstSyncTimeout(Exception): pass

        # reset sync counter for this iteration
        sync_count_60s = 0

        # Pre-connect DNS debug
        try:
            if creds.homeserver:
                _dns_debug("pre-connect", creds.homeserver)
        except Exception:
            pass

        # BLOCK until homeserver is reachable (progressive backoff + jitter)
        try:
            main_loop.run_until_complete(await_homeserver_ready(creds.homeserver or "", log,
                                                                min_backoff=5.0, max_backoff=600.0))
        except Exception:
            # If the preflight probe machinery itself failed, just log and continue (outer try/except will handle)
            log.exception("Preflight homeserver probe failed unexpectedly")

        bot    = connect_matrix(creds, log)
        helper = MatrixHelper(bot, main_loop, log)

        # Async health probes (non-fatal)
        if creds.homeserver:
            main_loop.create_task(_matrix_http_versions(creds.homeserver))
        main_loop.create_task(_matrix_whoami(bot, "post-connect"))

        # Response callback to count SyncResponses
        def _on_sync_response(resp: SyncResponse):
            nonlocal sync_count_60s
            sync_count_60s += 1

        # Fresh TS3Safe per iteration to avoid cross-iteration leakage
        ts3safe = TS3Safe(_ts3_connect_factory, _post_connect_register, log, notify_binder=bind_ts3_notifies)

        # Startup hook: loop exists; subscribe events and start heartbeat when API is ready
        @bot.listener.on_startup
        async def _on_startup(room: str):
            log.debug("on_startup fired for room=%s", room)
            global running_loop
            running_loop = asyncio.get_running_loop()
            helper.loop  = running_loop

            # Count SyncResponses
            try:
                if bot.async_client:
                    bot.async_client.add_response_callback(_on_sync_response, (SyncResponse,))
            except Exception:
                log.exception("Unable to install SyncResponse counter callback")

            # mark API ready & first-sync observed
            api_ready.set()
            startup_once.set()

            # subscribe TS3 events now that a loop exists; seed via ts3safe.call to trigger reconnect if needed
            try:
                def _ensure_binds_and_seed(c: TS3Connection):
                    setattr(c, "_notifies_bound", False)
                    bind_ts3_notifies(c, log)
                    now = time.time()
                    for cli in c.clientlist():
                        if cli.get("client_type") != "0":
                            continue
                        clid = cli["clid"]
                        client_names[clid] = cli.get("client_nickname", "?")
                        join_times[clid]   = now
                ts3safe.call(_ensure_binds_and_seed)
            except Exception:
                log.exception("TS3 subscribe/seed on startup failed")

            # start the presence heartbeat once
            nonlocal heartbeat_task
            if heartbeat_task is None or heartbeat_task.done():
                heartbeat_task = running_loop.create_task(_start_heartbeat())
                log.debug("presence heartbeat started")
            else:
                log.debug("presence heartbeat already running; skipping")

            ver_txt = "<unavailable>"
            try:
                ver_txt = ts3_version_safe(ts3safe)
            except Exception as e:
                log.warning("TS3 not ready on startup: %r", e)

            if not args.no_startup:
                try:
                    body, count = build_who_body()
                except Exception:
                    log.exception("startup who-body failed; falling back to simple announce")
                    body, count = ("👥 No clients online.", 0)
                if count > 0:
                    await bot.api.send_text_message(room, body)
                else:
                    await bot.api.send_text_message(
                        room,
                        f"🤖 Bot is online (TS3 v{ver_txt})\n{body}",
                    )

            if args.trace and bot.async_client:
                bot.async_client.add_response_callback(
                    lambda resp: log.sync("Full SyncResponse: %r", resp), (SyncResponse,)
                )

        @bot.listener.on_message_event
        async def on_commands(room, message):
            log.debug("message_event: sender=%s body=%r", message.sender, getattr(message, "body", None))
            match = botlib.MessageMatch(room, message, bot, prefix="!")
            if not match.is_not_from_this_bot():
                return
            cmd = (match.command() or "").lower()
            log.debug("parsed command=%r", cmd)
            # NOTE: ensure *room.room_id* everywhere (fixes AttributeError seen in logs)
            if   cmd in ("ping", "p"):           await handle_ping(room.room_id, message, bot, log)
            elif cmd in ("what",):               await handle_what(room.room_id, message, bot, log)
            elif cmd in ("where",):              await handle_where(room.room_id, message, bot, log)
            elif cmd in ("ts3health", "th"):     await handle_ts3health(room.room_id, bot, log)
            elif cmd in ("goodbot", "gb"):       await handle_goodbot(room.room_id, bot)
            elif cmd in ("badbot", "bb"):        await handle_badbot(room.room_id, bot, message, log)
            elif cmd in ("ts3online", "online", "who", "list"): await handle_ts3online(room.room_id, log, bot)
            elif cmd in ("restart", "rs"):       await handle_restart(room.room_id, message, bot, log)
            elif cmd in ("help", "h", "man"):    await handle_help(room.room_id, bot, log)
            elif cmd in ("debug", "d"):          await handle_debug(room.room_id, message, bot, log)

        async def _ts3_heartbeat():
            try:
                while True:
                    try:
                        _ = ts3_version_safe(ts3safe)  # triggers reconnect if needed
                    except Exception as e:
                        log.warning("TS3 heartbeat error: %r", e)
                    await asyncio.sleep(12)
            except asyncio.CancelledError:
                log.debug("_ts3_heartbeat cancelled")
                raise

        main_loop.create_task(_ts3_heartbeat())

        async def _start_heartbeat():
            await api_ready.wait()
            try:
                while True:
                    await reconcile_ts3_presence(bot, room_id, log)
                    await asyncio.sleep(10)
            except asyncio.CancelledError:
                log.debug("_start_heartbeat cancelled")
                raise

        async def run_bot_main():
            # If the homeserver goes away during run: let exceptions bubble, we’ll classify
            await bot.main()  # let CancelledError propagate to trigger restart

        try:
            log.info("Starting Matrix bot loop")

            bot_task = main_loop.create_task(run_bot_main())
            log.debug("bot_task created id=%s", id(bot_task))

            async def _first_sync_guard():
                try:
                    log.debug("first-sync guard armed (30s timeout)…")
                    await asyncio.wait_for(startup_once.wait(), timeout=30)
                    log.debug("first-sync guard satisfied (on_startup seen).")
                except asyncio.TimeoutError:
                    log.warning("Matrix first sync not observed within 30s; cancelling bot.main() and forcing restart.")
                    bot_task.cancel()
                    class FirstSyncTimeout(Exception): pass
                    raise FirstSyncTimeout()

            main_loop.create_task(_first_sync_guard())

            # Sync-rate watchdog: every 60s log rate; if 0 and first sync seen → restart
            async def _sync_rate_watchdog():
                nonlocal sync_count_60s
                try:
                    while True:
                        await asyncio.sleep(60)
                        count = sync_count_60s
                        sync_count_60s = 0
                        if startup_once.is_set() and count == 0:
                            log.warning("sync-rate watchdog: no SyncResponse in last 60s; restarting Matrix loop.")
                            bot_task.cancel()
                            return
                except asyncio.CancelledError:
                    return

            main_loop.create_task(_sync_rate_watchdog())

            # Optional time-based watchdog (disabled by default; enable with --watchdog)
            if args.watchdog:
                WATCHDOG_TIMEOUT = int(os.getenv("WATCHDOG_TIMEOUT", "1800"))  # default 30 min
                log.debug("watchdog armed (timeout=%ss)…", WATCHDOG_TIMEOUT)
                main_loop.run_until_complete(asyncio.wait_for(bot_task, timeout=WATCHDOG_TIMEOUT))
            else:
                log.debug("watchdog disabled; running bot_task without timeout")
                main_loop.run_until_complete(bot_task)

            if not startup_once.is_set():
                log.warning("Matrix main returned but no on_startup observed; restarting.")
            else:
                log.info("Matrix main returned; restarting bridge.")
            main_loop.run_until_complete(shutdown_bot(bot, log))

        except Exception as e:
            et = type(e).__name__
            # Classify expected outage/errors and back off quietly
            if is_transient_matrix_error(e) or et in {"FirstSyncTimeout"}:
                reason = f"{et}: {e}"
                if et == "FirstSyncTimeout":
                    _dump_debug_state(main_loop, bot, "first-sync timeout", bot_task)
                try:
                    main_loop.run_until_complete(shutdown_bot(bot, log))
                except Exception:
                    pass
                # Additional probes (best-effort)
                try:
                    if creds.homeserver:
                        main_loop.run_until_complete(_matrix_http_versions(creds.homeserver))
                    main_loop.run_until_complete(_matrix_whoami(bot, "post-shutdown-whoami"))
                except Exception:
                    pass
                log.warning("Transient Matrix error; will back off and retry: %s", reason)
            elif isinstance(e, asyncio.CancelledError):
                log.info("Event loop cancelled; performing graceful shutdown.")
                try:
                    main_loop.run_until_complete(shutdown_bot(bot, log))
                except Exception:
                    pass
            elif isinstance(e, KeyboardInterrupt):
                log.info("Shutdown via KeyboardInterrupt")
                main_loop.run_until_complete(shutdown_bot(bot, log))
                break
            else:
                # Unknown/unexpected → dump details then back off
                log.exception("Unexpected error in Matrix loop")
                _dump_debug_state(main_loop, bot, "unexpected error", bot_task)
                try:
                    main_loop.run_until_complete(shutdown_bot(bot, log))
                except Exception:
                    pass

        finally:
            async def _cancel_pending():
                # stop TS3 keepalive thread and quit *before* cancelling asyncio tasks
                try:
                    try:
                        ts3safe.conn.stop_keepalive_loop()
                    except Exception:
                        pass
                    _join_keepalive_if_present(ts3safe.conn, log)
                    try:
                        ts3safe.conn.quit()
                    except Exception:
                        pass
                    # ensure notify state is cleared on this object
                    try:
                        setattr(ts3safe.conn, "_notifies_bound", False)
                    except Exception:
                        pass
                except Exception:
                    log.exception("Error stopping TS3 connection during shutdown")

                tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                for t in tasks:
                    t.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(0)

            try:
                main_loop.run_until_complete(_cancel_pending())
            except Exception:
                pass
            finally:
                main_loop.close()

        # jittered backoff to avoid tight restart storms (outer loop)
        delay = min(600, backoff + random.uniform(0, max(1.0, backoff / 2)))
        log.info("Restarting in %.1fs… (backoff=%ss)", delay, backoff)
        try:
            if creds.homeserver:
                _dns_debug("pre-sleep", creds.homeserver)
        except Exception:
            pass
        time.sleep(delay)
        backoff = min(600, backoff * 2 or 5)
        try:
            if creds.homeserver:
                _dns_debug("pre-connect-retry", creds.homeserver)
        except Exception:
            pass

if __name__ == "__main__":
    main()
