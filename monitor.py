import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx
from config import (DEFAULT_HEADERS, EVENT_URL_TEMPLATE, MATCH_PAGE_URL,
                    MATCH_URL_TEMPLATE, STATE_FILE, WATCH_KEYWORDS)

logger = logging.getLogger("Monitor")


@dataclass
class Change:
    key: str        # unique id so the same alert is never sent twice
    headline: str
    body: str
    link: str


# --------------------------------------------------------------- fetching
class Fetcher:
    def __init__(self):
        self.retry_after = 0.0
        self._warned = set()

    def take_retry_after(self) -> float:
        value, self.retry_after = self.retry_after, 0.0
        return value

    async def get_json(self, client: httpx.AsyncClient, url: str):
        """Parsed JSON, or None on any failure."""
        try:
            r = await client.get(
                url,
                params={"_": int(time.time() * 1000)},   # same cache-buster the site uses
                headers=DEFAULT_HEADERS,
                timeout=10.0,
            )
        except httpx.RequestError as exc:
            logger.error("Request failed (%s): %s", url, type(exc).__name__)
            return None
        if r.status_code == 429:
            try:
                self.retry_after = float(r.headers.get("Retry-After", 60))
            except ValueError:
                self.retry_after = 60.0
            return None
        if r.status_code != 200:
            if url not in self._warned:
                logger.warning("HTTP %s from %s", r.status_code, url)
                self._warned.add(url)
            return None
        try:
            return r.json()
        except ValueError:
            logger.error("Response from %s was not JSON (blocked?).", url)
            return None


# ------------------------------------------------------------------ state
class State:
    """Per-feed snapshots. A feed whose snapshot is None gets a silent baseline."""
    KEYS = ("matches", "events", "queue")

    def __init__(self):
        self.data: Dict[str, Optional[dict]] = {k: None for k in self.KEYS}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, encoding="utf-8") as f:
                    loaded = json.load(f)
                for k in self.KEYS:
                    if isinstance(loaded.get(k), dict):
                        self.data[k] = loaded[k]
            except (OSError, ValueError):
                logger.warning("Could not read %s; starting fresh.", STATE_FILE)

    def save(self) -> None:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------- helpers
def _link(template: str, ident) -> str:
    return template.replace("{id}", str(ident)) if template else MATCH_PAGE_URL


def _watched(*texts) -> bool:
    if not WATCH_KEYWORDS:
        return True
    hay = " ".join(str(t) for t in texts if t).lower()
    return any(k in hay for k in WATCH_KEYWORDS)


def _future(iso) -> bool:
    try:
        return datetime.fromisoformat(iso) > datetime.now()
    except (TypeError, ValueError):
        return False


def _money(p) -> str:
    try:
        return f"{float(p):g}"
    except (TypeError, ValueError):
        return "?"


def _day(iso) -> str:
    return (iso or "")[:10]


# ---------------------------------------------------------------- matches
def _match_watched(m: dict) -> bool:
    t = m.get("tournament") or {}
    return _watched(m.get("teamName1"), m.get("teamNameAr1"), m.get("teamName2"),
                    m.get("teamNameAr2"), t.get("nameEn"), t.get("nameAr"))


def _describe_match(m: dict, note: str) -> str:
    t = (m.get("tournament") or {}).get("nameEn", "")
    return (f"{m.get('teamName1')} vs {m.get('teamName2')}\n{t}\n"
            f"Stadium: {m.get('stadiumName')}\nKickoff: {m.get('kickOffTime')}\n{note}")


def diff_matches(prev: Optional[dict], matches: list) -> Tuple[List[Change], dict]:
    changes: List[Change] = []
    snapshot: Dict[str, dict] = {}
    for m in matches:
        if not isinstance(m, dict) or "matchId" not in m:
            continue
        if m.get("isDeleted") or not m.get("showInPortal", True):
            continue
        mid = str(m["matchId"])
        cur = {"status": m.get("matchStatus"), "kickoff": m.get("kickOffTime")}
        snapshot[mid] = cur
        if prev is None or not _match_watched(m):
            continue
        link = _link(MATCH_URL_TEMPLATE, mid)
        old = prev.get(mid)
        if old is None:
            changes.append(Change(f"new:{mid}", "NEW MATCH LISTED",
                                  _describe_match(m, "Just appeared in the feed"), link))
            continue
        if old.get("status") != cur["status"]:
            changes.append(Change(f"status:{mid}:{old.get('status')}>{cur['status']}",
                                  "MATCH STATUS CHANGED",
                                  _describe_match(m, f"Status: {old.get('status')} -> {cur['status']}"), link))
        if old.get("kickoff") != cur["kickoff"]:
            changes.append(Change(f"kickoff:{mid}:{cur['kickoff']}", "KICKOFF TIME CHANGED",
                                  _describe_match(m, f"Kickoff: {old.get('kickoff')} -> {cur['kickoff']}"), link))
    return changes, snapshot


# ----------------------------------------------------------------- events
SHOW_FIELDS = {
    "portal": "Online (portal) status",
    "box": "Box-office status",
    "back": "Back-office status",
    "hand": "Handheld status",
    "start": "Show time",
}


def _show_snap(s: dict) -> dict:
    return {"name": (s.get("name") or "").strip(), "start": s.get("startDate"),
            "portal": s.get("portalStatus"), "back": s.get("backOfficeStatus"),
            "box": s.get("boxofficeStatus"), "hand": s.get("handheldStatus")}


def _event_snap(e: dict) -> dict:
    v = e.get("venue") or {}
    cat_en, cat_ar = e.get("eventCategoryNameEn"), e.get("eventCategoryNameAr")
    return {
        "name": (e.get("name") or "").strip(),
        "venue": (v.get("name") or "").strip(),
        "date": e.get("startDate"),
        "end": e.get("endDate"),
        "price": e.get("minimumPrice"),
        "watched": _watched(e.get("name"), e.get("nameAr"), v.get("name"), v.get("nameAr"), cat_en, cat_ar),
        "shows": {str(s["id"]): _show_snap(s) for s in (e.get("shows") or [])
                  if isinstance(s, dict) and "id" in s},
    }


def _event_text(s: dict, note: str) -> str:
    return (f"{s.get('name')}\nVenue: {s.get('venue')}\nDate: {_day(s.get('date'))}\n"
            f"From: {_money(s.get('price'))} EGP\n{note}")


def diff_events(prev: Optional[dict], events: list) -> Tuple[List[Change], dict]:
    changes: List[Change] = []
    snapshot: Dict[str, dict] = {}
    for e in events:
        if not isinstance(e, dict) or "id" not in e:
            continue
        eid = str(e["id"])
        snap = _event_snap(e)
        snapshot[eid] = snap
        if prev is None or not snap["watched"]:
            continue
        link = _link(EVENT_URL_TEMPLATE, eid)
        old = prev.get(eid)
        if old is None:
            changes.append(Change(f"ev-new:{eid}", "NEW EVENT LISTED",
                                  _event_text(snap, "Just appeared in the events feed"), link))
            continue
        if old.get("price") != snap["price"]:
            changes.append(Change(f"ev-price:{eid}:{snap['price']}", "PRICE CHANGED",
                                  _event_text(snap, f"Minimum price: {_money(old.get('price'))} -> {_money(snap['price'])}"), link))
        if old.get("date") != snap["date"]:
            changes.append(Change(f"ev-date:{eid}:{snap['date']}", "EVENT DATE CHANGED",
                                  _event_text(snap, f"Date: {_day(old.get('date'))} -> {_day(snap['date'])}"), link))
        old_shows = old.get("shows", {})
        for sid, s in snap["shows"].items():
            os_ = old_shows.get(sid)
            when = (s["start"] or "")[:16].replace("T", " ")
            if os_ is None:
                changes.append(Change(f"ev-show-new:{eid}:{sid}", "NEW SHOW ADDED",
                                      _event_text(snap, f"Show: {s['name']} ({when})"), link))
                continue
            diffs = [f"{label}: {os_.get(k)} -> {s[k]}" for k, label in SHOW_FIELDS.items()
                     if os_.get(k) != s[k]]
            if diffs:
                sig = ":".join(str(s[k]) for k in SHOW_FIELDS)
                changes.append(Change(f"ev-show:{eid}:{sid}:{sig}", "SHOW STATUS CHANGED",
                                      _event_text(snap, f"Show: {s['name']} ({when})\n" + "\n".join(diffs)), link))
        for sid, os_ in old_shows.items():
            if sid not in snap["shows"] and _future(os_.get("start")):
                changes.append(Change(f"ev-show-gone:{eid}:{sid}", "SHOW REMOVED FROM LIST",
                                      _event_text(snap, f"Show: {os_.get('name')} - may be sold out, cancelled or hidden"), link))
    if prev is not None:
        for eid, old in prev.items():
            if eid not in snapshot and old.get("watched") and _future(old.get("end")):
                changes.append(Change(f"ev-gone:{eid}", "EVENT REMOVED FROM LIST",
                                      _event_text(old, "May mean sold out, cancelled or hidden"),
                                      _link(EVENT_URL_TEMPLATE, eid)))
    return changes, snapshot


# ------------------------------------------------------------------ queue
def diff_queue(prev: Optional[dict], entries: list, match_index: Dict[str, dict]) -> Tuple[List[Change], dict]:
    snapshot: Dict[str, dict] = {}
    for q in entries:
        if isinstance(q, dict) and "matchId" in q:
            snapshot[str(q["matchId"])] = {"count": q.get("fanQueueCount"), "wait": q.get("queueWaitTime")}
    if prev is None:
        return [], snapshot

    def name_of(mid: str):
        m = match_index.get(mid)
        return (f"{m.get('teamName1')} vs {m.get('teamName2')}" if m else f"match #{mid}"), m

    changes: List[Change] = []
    for mid, cur in snapshot.items():
        name, m = name_of(mid)
        if m is not None and not _match_watched(m):
            continue
        old, link = prev.get(mid), _link(MATCH_URL_TEMPLATE, mid)
        body = f"{name}\nQueue size: {cur['count']}\nWait time: {cur['wait']}"
        if old is None:
            changes.append(Change(f"q-open:{mid}", "FAN QUEUE OPENED", body, link))
        elif old != cur:
            changes.append(Change(f"q-change:{mid}:{cur['count']}:{cur['wait']}", "FAN QUEUE CHANGED", body, link))
    for mid in prev:
        if mid not in snapshot:
            name, m = name_of(mid)
            if m is None or _match_watched(m):
                changes.append(Change(f"q-gone:{mid}", "FAN QUEUE REMOVED", name, _link(MATCH_URL_TEMPLATE, mid)))
    return changes, snapshot