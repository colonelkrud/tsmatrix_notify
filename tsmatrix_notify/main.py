from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import logging
import random
import signal
import time
from urllib.parse import urlparse
import threading
import uuid

import aiohttp
from dotenv import load_dotenv
import simplematrixbotlib as botlib
from nio import SyncResponse

from tsmatrix_notify.adapters.matrix_simplematrixbotlib import MatrixBotAdapter
from tsmatrix_notify.adapters.persistence_fs import FilePersistence
from tsmatrix_notify.adapters.ts3_ts3api import TS3APIAdapter
from tsmatrix_notify.config import ConfigError, load_config
from tsmatrix_notify.health import HealthServer, HealthState
from tsmatrix_notify.domain.events import TSEvent
from tsmatrix_notify.domain.handlers import handle_ts_event, reconcile_presence
from tsmatrix_notify.domain.messages import build_who_body
from tsmatrix_notify.domain.state import AppState
from tsmatrix_notify.application.dispatcher import send_actions, send_actions_if_ready
from tsmatrix_notify.application.supervisors import (
    MatrixReconnectSupervisor,
    SyncWatchdogState,
    TS3ReconnectSupervisor,
    install_ts3_thread_excepthook,
)


SYNC_LEVEL_NUM = 5
logging.addLevelName(SYNC_LEVEL_NUM, "SYNC")


def _sync(self: logging.Logger, message: str, *args: object, **kwargs: object) -> None:
    if self.isEnabledFor(SYNC_LEVEL_NUM):
        self._log(SYNC_LEVEL_NUM, message, args, **kwargs)  # type: ignore[arg-type]  # pylint: disable=W0212


setattr(logging.Logger, "sync", _sync)


def setup_logger(debug: bool, trace: bool):
    if trace:
        lvl = SYNC_LEVEL_NUM
    elif debug:
        lvl = logging.DEBUG
    else:
        lvl = logging.INFO
    log = logging.getLogger("TSMatrixNotify")
    log.setLevel(lvl)
    handler = logging.StreamHandler()
    class _StructuredContextFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            for key in (
                "correlation_id",
                "event_type",
                "ts3_event",
                "ts3_client_id",
                "ts3_client_name",
                "matrix_room_id",
                "restart_reason",
                "sync_count",
                "last_successful_sync_at",
                "seconds_since_last_sync",
                "send_attempt",
                "error_type",
            ):
                if not hasattr(record, key):
                    setattr(record, key, "-")
            return True

    handler.addFilter(_StructuredContextFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)5s %(message)s "
            "corr=%(correlation_id)s event=%(event_type)s ts3_event=%(ts3_event)s "
            "clid=%(ts3_client_id)s cname=%(ts3_client_name)s room=%(matrix_room_id)s "
            "restart=%(restart_reason)s sync_count=%(sync_count)s last_sync=%(last_successful_sync_at)s "
            "since_last=%(seconds_since_last_sync)s attempt=%(send_attempt)s err=%(error_type)s"
        )
    )
    log.addHandler(handler)
    if debug or trace:
        logging.getLogger("nio").setLevel(logging.DEBUG)
        logging.getLogger("nio.client").setLevel(logging.DEBUG)
        logging.getLogger("aiohttp").setLevel(logging.WARNING)
    return log


def parse_args():
    parser = argparse.ArgumentParser("TSMatrixNotify bridge")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("-t", "--trace", action="store_true", help="Enable full SyncResponse tracing")
    parser.add_argument("--no-startup", action="store_true", help="Don't send the startup announcement to Matrix")
    parser.add_argument(
        "--watchdog",
        action="store_true",
        default=False,
        help="Enable time-based watchdog (timeout from WATCHDOG_TIMEOUT env; default 1800s)",
    )
    return parser.parse_args()


async def shutdown_bot(bot, log):
    try:
        client = getattr(bot, "async_client", None)
        if client:
            log.debug("Closing Matrix aiohttp session…")
            await client.close()
            log.info("Matrix session closed.")
    except Exception:
        log.exception("Error during bot shutdown")


async def probe_homeserver(hs: str, log: logging.Logger, timeout_s: int = 6) -> bool:
    if not hs:
        log.error("Matrix homeserver not configured")
        return False
    log.debug("versions probe using homeserver=%r", hs)
    url = hs.rstrip("/") + "/_matrix/client/versions"
    try:
        async with aiohttp.ClientSession() as session:
            timeout = aiohttp.ClientTimeout(total=float(timeout_s))
            async with session.get(url, timeout=timeout) as response:
                _ = await response.read()
                ok = 200 <= response.status < 300
                if ok:
                    log.info("versions probe: %s -> %s", url, response.status)
                else:
                    log.warning("versions probe non-OK: %s -> %s", url, response.status)
                return ok
    except Exception as exc:
        log.warning("versions probe failed: %r", exc)
        return False


async def await_homeserver_ready(
    hs: str,
    log: logging.Logger,
    min_backoff: float = 5.0,
    max_backoff: float = 600.0,
    stop_event: threading.Event | None = None,
) -> bool:
    delay = min_backoff
    while True:
        if stop_event and stop_event.is_set():
            return False
        ok = await probe_homeserver(hs, log)
        if ok:
            return True
        jitter = random.uniform(0, delay / 2)
        wait = min(max_backoff, delay + jitter)
        log.info("Matrix unavailable; retrying in %.1fs (backoff=%ss)", wait, int(delay))
        elapsed = 0.0
        while elapsed < wait:
            if stop_event and stop_event.is_set():
                return False
            step = min(1.0, wait - elapsed)
            await asyncio.sleep(step)
            elapsed += step
        delay = min(max_backoff, delay * 2 or min_backoff)


def validate_and_normalize_homeserver(hs: str, log: logging.Logger) -> str:
    raw = "" if hs is None else str(hs)
    log.debug("Validating Matrix homeserver: %r", raw)
    candidate = raw.strip()
    parsed = urlparse(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        log.error("Invalid Matrix homeserver: %r", raw)
        raise ConfigError(f"Invalid Matrix homeserver: {raw!r}")
    normalized = candidate.rstrip("/")
    if normalized != candidate:
        log.debug("Normalized Matrix homeserver: %r -> %r", candidate, normalized)
    return normalized


def build_matrix_creds(matrix_config, log: logging.Logger, normalized_homeserver: str | None = None):
    normalized_hs = normalized_homeserver or validate_and_normalize_homeserver(
        matrix_config.homeserver, log
    )
    creds = botlib.Creds(
        homeserver=normalized_hs,
        username=matrix_config.user_id,
        access_token=matrix_config.access_token,
        session_stored_file=matrix_config.session_file,
    )
    return normalized_hs, creds


def connect_matrix(creds, log):
    log.debug("Initializing Matrix bot for %s (homeserver=%r)", creds.username, creds.homeserver)
    bot = botlib.Bot(creds)
    log.info("Matrix bot created for %s", creds.username)
    return bot


def run() -> int:  # pragma: no cover - exercised via integration/runtime execution
    args = parse_args()
    log = setup_logger(args.debug, args.trace)
    load_dotenv()
    log.info("tsmatrix_notify.py starting…")
    ts3_restart_event = threading.Event()
    stop_event = threading.Event()
    install_ts3_thread_excepthook(ts3_restart_event, log)

    try:
        config = load_config(log)
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2

    health_state = HealthState(live=True, ready=False, status="starting")
    health_server = HealthServer(
        config.health.host,
        config.health.port,
        config.health.path_live,
        config.health.path_ready,
        health_state,
        log,
    )
    health_server.start()

    runtime: dict[str, asyncio.AbstractEventLoop | asyncio.Task[object] | None] = {"loop": None, "bot_task": None}

    def _signal_handler(signum, _frame):
        signame = signal.Signals(signum).name
        log.info("Received %s; beginning graceful shutdown.", signame)
        stop_event.set()
        health_state.set_ready(False, "shutting down")
        health_state.set_live(False, "shutting down")
        loop = runtime.get("loop")
        bot_task = runtime.get("bot_task")
        if isinstance(loop, asyncio.AbstractEventLoop) and isinstance(bot_task, asyncio.Task) and not bot_task.done():
            loop.call_soon_threadsafe(bot_task.cancel)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    persistence = FilePersistence(config.bot_messages_file, config.stats_path, log)
    messages, apologies = persistence.load_message_catalog()

    state = AppState()
    sync_watchdog = SyncWatchdogState(stall_threshold_s=60)
    matrix_supervisor = MatrixReconnectSupervisor(log)
    restart_delay: float | None = None

    while not stop_event.is_set():
        main_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(main_loop)
        runtime["loop"] = main_loop

        api_ready = asyncio.Event()
        heartbeat_task = None
        startup_once = asyncio.Event()
        bot = None
        ts3 = None
        ts3_supervisor = None
        normalized_homeserver = None

        try:
            health_state.set_ready(False, "starting")
            normalized_homeserver = validate_and_normalize_homeserver(config.matrix.homeserver, log)
            if normalized_homeserver:
                homeserver_ready = main_loop.run_until_complete(
                    await_homeserver_ready(normalized_homeserver, log, stop_event=stop_event)
                )
                if not homeserver_ready:
                    log.info("Shutdown requested while waiting for homeserver readiness.")
                    break

            _, creds = build_matrix_creds(
                config.matrix, log, normalized_homeserver=normalized_homeserver
            )
            room_id = config.matrix.room_id

            bot = connect_matrix(creds, log)
            matrix = MatrixBotAdapter(bot, main_loop, log)
            ts3 = TS3APIAdapter(
                config.ts3.host,
                config.ts3.port,
                config.ts3.user,
                config.ts3.password,
                config.ts3.vserver_id,
                log,
            )
            ts3_restart_event.clear()
            ts3_supervisor = TS3ReconnectSupervisor(ts3, ts3_restart_event, log)

            def _on_sync_response(resp: SyncResponse):
                sync_watchdog.mark_sync_success(time.time())

            async def _matrix_http_versions(hs: str, timeout_s: int = 6):
                url = hs.rstrip("/") + "/_matrix/client/versions"
                try:
                    async with aiohttp.ClientSession() as session:
                        timeout = aiohttp.ClientTimeout(total=float(timeout_s))
                        async with session.get(url, timeout=timeout) as response:
                            txt = await response.text()
                            log.info("versions probe: %s -> %s len=%d", url, response.status, len(txt))
                except Exception as exc:
                    log.warning("versions probe failed: %r", exc)

            async def _matrix_whoami(tag: str, timeout_s: int = 8):
                try:
                    ac = getattr(bot, "async_client", None)
                    if not ac:
                        log.debug("%s whoami: no async_client yet", tag)
                        return
                    res = await asyncio.wait_for(ac.whoami(), timeout=timeout_s)
                    log.info("%s whoami: OK user_id=%s", tag, getattr(res, "user_id", None))
                except asyncio.TimeoutError:
                    log.warning("%s whoami: TIMEOUT after %ss", tag, timeout_s)
                except Exception as exc:
                    log.warning("%s whoami: %r", tag, exc)

            if normalized_homeserver:
                main_loop.create_task(_matrix_http_versions(normalized_homeserver))
            main_loop.create_task(_matrix_whoami("post-connect"))

            def ts3_event_handler(event: TSEvent):
                correlation_id = event.correlation_id or str(uuid.uuid4())
                log.info("ts3_notify_received", extra={"correlation_id": correlation_id, "event_type": event.kind, "ts3_event": event.kind, "ts3_client_id": event.data.get("clid"), "ts3_client_name": event.data.get("client_nickname")})
                translated = TSEvent(kind=event.kind, data=event.data, correlation_id=correlation_id)
                actions = handle_ts_event(translated, state, room_id, time.time())
                log.info("dispatch_decision", extra={"correlation_id": correlation_id, "event_type": event.kind, "matrix_room_id": room_id, "action_count": len(actions)})
                send_actions(matrix, actions, log)

            ts3.register_event_handler(ts3_event_handler)

            @bot.listener.on_startup
            async def _on_startup(room: str):
                nonlocal heartbeat_task
                log.debug("on_startup fired for room=%s", room)
                matrix.loop = asyncio.get_running_loop()
                matrix_supervisor.reset()

                try:
                    if bot.async_client:
                        bot.async_client.add_response_callback(_on_sync_response, (SyncResponse,))
                except Exception:
                    log.exception("Unable to install SyncResponse counter callback")

                api_ready.set()
                startup_once.set()
                health_state.set_ready(True, "ready")

                now = time.time()
                for client in ts3.clientlist():
                    if client.get("client_type") != "0":
                        continue
                    clid = client["clid"]
                    state.client_names[clid] = client.get("client_nickname", "?")
                    state.join_times[clid] = now

                if heartbeat_task is None or heartbeat_task.done():
                    heartbeat_task = matrix.loop.create_task(_start_heartbeat())

                ver_txt = "<unavailable>"
                try:
                    ver_txt = ts3.version()
                except Exception as exc:
                    log.warning("TS3 not ready on startup: %r", exc)

                if not args.no_startup:
                    try:
                        body, count = build_who_body(
                            ts3.clientlist(), ts3.clientinfo, state.join_times, time.time()
                        )
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
                async def handle_ping():
                    sent_ts = message.source.get("origin_server_ts", 0)
                    now_ms = int(time.time() * 1000)
                    latency = max(0, now_ms - int(sent_ts))
                    await bot.api.send_text_message(room.room_id, f"PONG! Latency: {latency} ms")

                async def handle_ts3health():
                    try:
                        ver_txt = ts3.version()
                        await bot.api.send_text_message(
                            room.room_id, f"✅ TS3 reachable. Version: {ver_txt}"
                        )
                    except Exception as exc:
                        await bot.api.send_text_message(
                            room.room_id, f"❌ TS3 health check failed: {exc!r}"
                        )

                async def handle_goodbot():
                    stats = persistence.load_stats()
                    stats["good"] = int(stats.get("good", 0)) + 1
                    persistence.save_stats(stats)
                    await bot.api.send_text_message(room.room_id, random.choice(messages))
                    total = int(stats.get("good", 0)) + int(stats.get("bad", 0))
                    await bot.api.send_text_message(
                        room.room_id,
                        (
                            "Review summary — 👍: "
                            f"{stats.get('good', 0)}, 👎: {stats.get('bad', 0)} total reviews: {total}"
                        ),
                    )

                async def handle_badbot():
                    stats = persistence.load_stats()
                    stats["bad"] = int(stats.get("bad", 0)) + 1
                    persistence.save_stats(stats)
                    apology = random.choice(apologies)
                    await bot.api.send_text_message(room.room_id, apology)
                    total = int(stats.get("good", 0)) + int(stats.get("bad", 0))
                    await bot.api.send_text_message(
                        room.room_id,
                        (
                            "Review summary — 👍: "
                            f"{stats.get('good', 0)}, 👎: {stats.get('bad', 0)} total reviews: {total}"
                        ),
                    )

                async def handle_ts3online():
                    try:
                        body, _ = build_who_body(
                            ts3.clientlist(), ts3.clientinfo, state.join_times, time.time()
                        )
                        await bot.api.send_text_message(room.room_id, body)
                    except Exception as exc:
                        log.exception("ts3online: error fetching clients")
                        await bot.api.send_text_message(
                            room.room_id,
                            f"❌ Could not fetch online users: {exc!r}",
                        )

                async def handle_help():
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
                    await bot.api.send_text_message(room.room_id, help_text)

                match = botlib.MessageMatch(room, message, bot, prefix="!")
                if not match.is_not_from_this_bot():
                    return
                cmd = (match.command() or "").lower()
                if cmd in ("ping", "p"):
                    await handle_ping()
                elif cmd in ("what",):
                    await bot.api.send_text_message(room.room_id, "When?")
                elif cmd in ("where",):
                    await bot.api.send_text_message(room.room_id, "and Why do I delight in??")
                elif cmd in ("ts3health", "th"):
                    await handle_ts3health()
                elif cmd in ("goodbot", "gb"):
                    await handle_goodbot()
                elif cmd in ("badbot", "bb"):
                    await handle_badbot()
                elif cmd in ("ts3online", "online", "who", "list"):
                    await handle_ts3online()
                elif cmd in ("restart", "rs"):
                    await bot.api.send_text_message(room.room_id, "🔄 Restarting bot…")
                    raise RestartBot()
                elif cmd in ("help", "h", "man"):
                    await handle_help()
                elif cmd in ("debug", "d"):
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    ts3_target = (
                        f"{config.ts3.host}:{config.ts3.port} (vsid={config.ts3.vserver_id})"
                    )
                    try:
                        inst = ts3.hostinfo()
                        inst_secs = int(inst.get("instance_uptime", 0))
                        days, rem = divmod(inst_secs, 86400)
                        hours, rem = divmod(rem, 3600)
                        mins, secs = divmod(rem, 60)
                        inst_uptime = f"{days}d{hours}h{mins}m{secs}s"
                    except Exception:
                        inst_uptime = "<error>"

                    try:
                        vs = ts3.serverinfo()
                        vs_secs = int(vs.get("virtualserver_uptime", 0))
                        days, rem = divmod(vs_secs, 86400)
                        hours, rem = divmod(rem, 3600)
                        mins, secs = divmod(rem, 60)
                        vs_uptime = f"{days}d{hours}h{mins}m{secs}s"
                    except Exception:
                        vs_uptime = "<error>"

                    try:
                        who = await bot.async_client.whoami()
                        mx_user_id = getattr(who, "user_id", config.matrix.user_id)
                    except Exception:
                        mx_user_id = config.matrix.user_id
                    summary = (
                        "🛠️ **DEBUG SUMMARY**\n"
                        f"- UTC now:                   {now}\n"
                        f"- TS3 target:                {ts3_target}\n"
                        f"- TS3 instance uptime:       {inst_uptime}\n"
                        f"- TS3 virtualserver uptime:  {vs_uptime}\n"
                        f"- Matrix homeserver:         {normalized_homeserver}\n"
                        f"- Matrix user_id (whoami):   {mx_user_id}\n"
                        f"- Matrix room:               {room_id}\n"
                    )
                    await bot.api.send_text_message(room.room_id, summary)

                    sequence = [
                        ("ping", handle_ping),
                        ("ts3health", handle_ts3health),
                        ("goodbot", handle_goodbot),
                        ("ts3online", handle_ts3online),
                        ("help", handle_help),
                    ]
                    for name, fn in sequence:
                        await bot.api.send_text_message(room.room_id, f"▶️ Debug: testing !{name}")
                        await fn()
                        await asyncio.sleep(3)
                    await bot.api.send_text_message(room.room_id, "✅ Debug run complete.")

            async def _ts3_heartbeat():
                try:
                    while True:
                        try:
                            _ = ts3.version()
                        except Exception as exc:
                            log.warning("TS3 heartbeat error: %r", exc)
                            if ts3_supervisor:
                                ts3_supervisor.request_restart(f"heartbeat error: {exc!r}")
                        await asyncio.sleep(12)
                except asyncio.CancelledError:
                    log.debug("_ts3_heartbeat cancelled")
                    raise

            main_loop.create_task(_ts3_heartbeat())

            async def _ts3_restart_watch():
                try:
                    log.info("TS3 restart supervisor started")
                    while True:
                        if ts3_restart_event.is_set():
                            log.warning("TS3 restart signal observed; attempting reconnect")
                            try:
                                await asyncio.to_thread(ts3_supervisor.reconnect_with_backoff)
                            except Exception:
                                log.exception("TS3 reconnect supervisor loop failed")
                        await asyncio.sleep(1)
                except asyncio.CancelledError:
                    log.debug("_ts3_restart_watch cancelled")
                    raise

            if ts3_supervisor:
                main_loop.create_task(_ts3_restart_watch())

            async def _start_heartbeat():
                await api_ready.wait()
                try:
                    while True:
                        actions = reconcile_presence(ts3.clientlist(), state, room_id, time.time())
                        send_actions_if_ready(matrix, actions, log)
                        await asyncio.sleep(10)
                except asyncio.CancelledError:
                    log.debug("_start_heartbeat cancelled")
                    raise

            async def run_bot_main():
                await bot.main()

            bot_task = main_loop.create_task(run_bot_main())
            runtime["bot_task"] = bot_task

            async def _sync_rate_watchdog():
                try:
                    while True:
                        await asyncio.sleep(60)
                        count = sync_watchdog.consume_interval_count()
                        if startup_once.is_set() and count == 0:
                            ctx = sync_watchdog.stall_context(time.time())
                            ctx["sync_count"] = count
                            log.warning("matrix_sync_stalled", extra=ctx)
                            health_state.set_ready(False, "matrix sync stalled; restarting")
                            bot_task.cancel()
                            return
                except asyncio.CancelledError:
                    return

            main_loop.create_task(_sync_rate_watchdog())

            if args.watchdog:
                main_loop.run_until_complete(
                    asyncio.wait_for(bot_task, timeout=config.watchdog_timeout)
                )
            else:
                main_loop.run_until_complete(bot_task)

            if not startup_once.is_set():
                health_state.set_ready(False, "startup incomplete; restarting")
                log.warning("Matrix main returned but no on_startup observed; restarting.")
            else:
                health_state.set_ready(False, "restarting")
                log.info("Matrix main returned; restarting bridge.")
            main_loop.run_until_complete(shutdown_bot(bot, log))

        except ConfigError as exc:
            log.warning("matrix_sync_exception", extra={"restart_reason": "matrix_sync_exception", "error_type": type(exc).__name__})
            restart_delay = matrix_supervisor.handle_error(exc)
            try:
                main_loop.run_until_complete(shutdown_bot(bot, log))
            except Exception:
                pass
        except Exception as exc:
            et = type(exc).__name__
            if isinstance(exc, asyncio.CancelledError):
                log.info("Event loop cancelled; performing graceful shutdown.")
                try:
                    main_loop.run_until_complete(shutdown_bot(bot, log))
                except Exception:
                    pass
            elif isinstance(exc, KeyboardInterrupt):
                log.info("Shutdown via KeyboardInterrupt")
                stop_event.set()
                main_loop.run_until_complete(shutdown_bot(bot, log))
                break
            else:
                if et in {"RestartBot"}:
                    try:
                        main_loop.run_until_complete(shutdown_bot(bot, log))
                    except Exception:
                        pass
                    restart_delay = matrix_supervisor.next_delay()
                    health_state.set_ready(False, "manual restart requested")
                    log.warning("shutdown_requested", extra={"restart_reason": "shutdown_requested"})
                    log.warning("Restart requested; retrying in %.1fs", restart_delay)
                else:
                    health_state.set_ready(False, f"error: {et}")
                    log.warning("matrix_sync_exception", extra={"restart_reason": "matrix_sync_exception", "error_type": et})
                    restart_delay = matrix_supervisor.handle_error(exc)
                try:
                    main_loop.run_until_complete(shutdown_bot(bot, log))
                except Exception:
                    pass

        finally:
            async def _cancel_pending():
                tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(0)

            try:
                main_loop.run_until_complete(_cancel_pending())
            except Exception:
                pass
            finally:
                if ts3:
                    try:
                        ts3.close()
                    except Exception:
                        log.exception("Error stopping TS3 connection during shutdown")
                main_loop.close()
                runtime["bot_task"] = None
                runtime["loop"] = None

        if stop_event.is_set():
            break
        delay = restart_delay if restart_delay is not None else matrix_supervisor.next_delay()
        log.info("Restarting in %.1fs…", delay)
        stop_event.wait(timeout=delay)
        restart_delay = None

    health_server.stop()
    return 0


class RestartBot(Exception):
    """Internal signal to restart the bot gracefully."""


def main():
    raise SystemExit(run())
