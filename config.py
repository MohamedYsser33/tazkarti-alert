import os
from pathlib import Path


def _load_env(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

# ---- Data feeds (public files the Tazkarti site itself loads) ----
DATA_BASE = os.getenv("DATA_BASE", "https://tazkarti.com/data")
MATCHES_URL = f"{DATA_BASE}/matches-list-json.json"
EXTRA_FEEDS = {} if os.getenv("DISABLE_EXTRA_FEEDS") == "1" else {
    "events": f"{DATA_BASE}/events-list-json.json",
    "queue": f"{DATA_BASE}/fanQueuesMatch-list-json.json",
}

# ---- Links used inside alerts ----
MATCH_PAGE_URL = os.getenv("MATCH_PAGE_URL", "https://www.tazkarti.com/#/")
EVENT_URL_TEMPLATE = os.getenv("EVENT_URL_TEMPLATE", "")
MATCH_URL_TEMPLATE = os.getenv("MATCH_URL_TEMPLATE", "")

# ---- Random polling interval (seconds). Never below 5. ----
POLL_MIN_SECONDS = max(5.0, float(os.getenv("POLL_MIN_SECONDS", "5")))
POLL_MAX_SECONDS = max(POLL_MIN_SECONDS, float(os.getenv("POLL_MAX_SECONDS", "10")))

STATE_FILE = os.getenv("STATE_FILE", "state.json")

# ---- Filtering (empty = watch everything) ----
WATCH_KEYWORDS = [k.strip().lower() for k in os.getenv("WATCH_KEYWORDS", "").split(",") if k.strip()]

# ---- Telegram ----
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}