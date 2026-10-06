import asyncio
import logging
import os
import random
import signal
import sys
import time

import httpx
from config import (EXTRA_FEEDS, MATCHES_URL, MATCH_PAGE_URL,
                    POLL_MAX_SECONDS, POLL_MIN_SECONDS)
from monitor import Fetcher, State, diff_events, diff_matches, diff_queue
from notifier import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)    # keeps your bot token out of the logs
logger = logging.getLogger("Main")

FEEDS = {"matches": MATCHES_URL, **EXTRA_FEEDS}
RUN_SECONDS = float(os.getenv("RUN_SECONDS", "0"))      # 0 = run forever; CI sets a limit


async def run(test: bool) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:      # Windows: Ctrl+C is handled below
            pass

    async with httpx.AsyncClient() as client:
        notifier = TelegramNotifier(client)

        if test:
            if not notifier.configured:
                print("Token or chat ID missing. Check your .env file.")
                return
            ok = await notifier.send_alert("TEST ALERT", "If you see this, Telegram works.", MATCH_PAGE_URL)
            print("Test message sent - check Telegram." if ok else "Sending failed - check token and chat ID.")
            return

        fetcher, state = Fetcher(), State()
        sent: set = set()
        failures = 0
        fresh = [k for k in FEEDS if state.data.get(k) is None]
        logger.info("Started. Random polling every %.0f-%.0fs. Silent baseline for: %s",
                    POLL_MIN_SECONDS, POLL_MAX_SECONDS, ", ".join(fresh) or "none")

        deadline = time.monotonic() + RUN_SECONDS if RUN_SECONDS > 0 else None
        while not stop.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                logger.info("Run time limit reached.")
                break
            results = await asyncio.gather(*(fetcher.get_json(client, u) for u in FEEDS.values()))
            raw = {n: (r if isinstance(r, list) else None) for n, r in zip(FEEDS, results)}

            if all(v is None for v in raw.values()):
                failures += 1
                delay = min(300.0, POLL_MAX_SECONDS * 2 ** failures)         # backoff on errors
            else:
                failures = 0
                delay = random.uniform(POLL_MIN_SECONDS, POLL_MAX_SECONDS)   # random 5-10s
            delay = max(delay, fetcher.take_retry_after())

            changes, pending = [], {}
            if raw.get("matches") is not None:
                found, pending["matches"] = diff_matches(state.data["matches"], raw["matches"])
                changes += found
            if raw.get("events") is not None:
                found, pending["events"] = diff_events(state.data["events"], raw["events"])
                changes += found
            if raw.get("queue") is not None:
                index = {str(m["matchId"]): m for m in (raw.get("matches") or [])
                         if isinstance(m, dict) and "matchId" in m}
                found, pending["queue"] = diff_queue(state.data["queue"], raw["queue"], index)
                changes += found

            if pending:
                all_sent = True
                for c in changes:
                    if c.key in sent:
                        continue
                    if await notifier.send_alert(c.headline, c.body, c.link):
                        sent.add(c.key)
                    else:
                        all_sent = False                     # retry next cycle
                if all_sent:                                 # remember state only once alerts are out
                    state.data.update(pending)
                    state.save()
                    sent.clear()
                logger.info("Checked %s | %d change(s) | next in %.1fs",
                            ", ".join(f"{k}:{len(v)}" for k, v in pending.items()), len(changes), delay)

            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
    logger.info("Stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(run(test="--test" in sys.argv))
    except KeyboardInterrupt:
        pass