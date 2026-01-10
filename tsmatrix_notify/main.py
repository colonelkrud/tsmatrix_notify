from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import logging
import random
import sys
import time

import aiohttp
from dotenv import load_dotenv
import simplematrixbotlib as botlib
from nio import SyncResponse

from tsmatrix_notify.adapters.matrix_simplematrixbotlib import MatrixBotAdapter
from tsmatrix_notify.adapters.persistence_fs import FilePersistence
from tsmatrix_notify.adapters.ts3_ts3api import TS3APIAdapter
from tsmatrix_notify.config import ConfigError, load_config
from tsmatrix_notify.domain.events import TSEvent
from tsmatrix_notify.domain.handlers import handle_ts_event, reconcile_presence
from tsmatrix_notify.domain.messages import build_who_body
from tsmatrix_notify.domain.state import AppState
from tsmatrix_notify.application.dispatcher import send_actions, send_actions_if_ready


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
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)5s %(message)s"))
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
    url = hs.rstrip("/") + "/_matrix/client/versions"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout_s) as response:
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
) -> None:
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
    from aiohttp.client_exceptions import ClientConnectorError

    transient_types = (asyncio.TimeoutError, TimeoutError, ClientConnectorError, ConnectionError)
    if isinstance(exc, transient_types):
        return True
    if isinstance(exc, ValueError) and "Invalid Homeserver" in str(exc):
        return True
    if isinstance(exc, Exception) and "M_UNKNOWN" in str(exc):
        return True
    return False


def connect_matrix(creds, log):
    while True:
        try:
            if not creds.homeserver or not str(creds.homeserver).strip().lower().startswith(("http://", "https://")):
                raise ValueError("Invalid Homeserver")
            log.debug("Initializing Matrix bot for %s", creds.username)
            bot = botlib.Bot(creds)
            log.info("Matrix bot created for %s", creds.username)
            return bot
        except ValueError as exc:
            log.error("Matrix init failed: %s", exc)
            time.sleep(5)
        except Exception as exc:
            log.error("Matrix init failed: %s", exc)
            time.sleep(5)


def run() -> int:
    args = parse_args()
    log = setup_logger(args.debug, args.trace)
    load_dotenv()
    log.info("tsmatrix_notify.py starting…")

    try:
        config = load_config(log)
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2

    persistence = FilePersistence(config.bot_messages_file, config.stats_path, log)
    messages, apologies = persistence.load_message_catalog()

    state = AppState()
    backoff = 5
    sync_count_60s = 0

    while True:
        main_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(main_loop)

        api_ready = asyncio.Event()
        heartbeat_task = None
        startup_once = asyncio.Event()
        bot = None
        ts3 = None

        try:
            if config.matrix.homeserver:
                main_loop.run_until_complete(await_homeserver_ready(config.matrix.homeserver, log))

            creds = botlib.Creds(
                homeserver=config.matrix.homeserver,
                username=config.matrix.user_id,
                access_token=config.matrix.access_token,
                session_stored_file=config.matrix.session_file,
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

            def _on_sync_response(resp: SyncResponse):
                nonlocal sync_count_60s
                sync_count_60s += 1

            async def _matrix_http_versions(hs: str, timeout_s: int = 6):
                url = hs.rstrip("/") + "/_matrix/client/versions"
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=timeout_s) as response:
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

            if config.matrix.homeserver:
                main_loop.create_task(_matrix_http_versions(config.matrix.homeserver))
            main_loop.create_task(_matrix_whoami("post-connect"))

            def ts3_event_handler(event: TSEvent):
                actions = handle_ts_event(event, state, room_id, time.time())
                send_actions(matrix, actions, log)

            ts3.register_event_handler(ts3_event_handler)

            @bot.listener.on_startup
            async def _on_startup(room: str):
                nonlocal heartbeat_task
                log.debug("on_startup fired for room=%s", room)
                matrix.loop = asyncio.get_running_loop()

                try:
                    if bot.async_client:
                        bot.async_client.add_response_callback(_on_sync_response, (SyncResponse,))
                except Exception:
                    log.exception("Unable to install SyncResponse counter callback")

                api_ready.set()
                startup_once.set()

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
                        f"- Matrix homeserver:         {config.matrix.homeserver}\n"
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
                        await asyncio.sleep(12)
                except asyncio.CancelledError:
                    log.debug("_ts3_heartbeat cancelled")
                    raise

            main_loop.create_task(_ts3_heartbeat())

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

            if args.watchdog:
                main_loop.run_until_complete(
                    asyncio.wait_for(bot_task, timeout=config.watchdog_timeout)
                )
            else:
                main_loop.run_until_complete(bot_task)

            if not startup_once.is_set():
                log.warning("Matrix main returned but no on_startup observed; restarting.")
            else:
                log.info("Matrix main returned; restarting bridge.")
            main_loop.run_until_complete(shutdown_bot(bot, log))

        except Exception as exc:
            et = type(exc).__name__
            if is_transient_matrix_error(exc) or et in {"RestartBot"}:
                reason = f"{et}: {exc}"
                try:
                    main_loop.run_until_complete(shutdown_bot(bot, log))
                except Exception:
                    pass
                log.warning("Transient Matrix error; will back off and retry: %s", reason)
            elif isinstance(exc, asyncio.CancelledError):
                log.info("Event loop cancelled; performing graceful shutdown.")
                try:
                    main_loop.run_until_complete(shutdown_bot(bot, log))
                except Exception:
                    pass
            elif isinstance(exc, KeyboardInterrupt):
                log.info("Shutdown via KeyboardInterrupt")
                main_loop.run_until_complete(shutdown_bot(bot, log))
                break
            else:
                log.exception("Unexpected error in Matrix loop")
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

        delay = min(600, backoff + random.uniform(0, max(1.0, backoff / 2)))
        log.info("Restarting in %.1fs… (backoff=%ss)", delay, backoff)
        time.sleep(delay)
        backoff = min(600, backoff * 2 or 5)

    return 0


class RestartBot(Exception):
    """Internal signal to restart the bot gracefully."""


def main():
    raise SystemExit(run())
