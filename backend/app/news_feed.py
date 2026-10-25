"""Football headlines from public RSS feeds, for the app's news banner.

Headlines only -- title, link, source, time -- each linking out to the
publisher's own page. The article text is never copied: it isn't ours to
republish, and a headline that sends the reader to the source is what these
feeds are offered for.

The feeds are fetched at most every ``TTL`` and shared by every viewer, so the
cost of any number of page views is one small request per feed per quarter
hour. An expired cache is served while a background refresh replaces it, so a
member never waits on a publisher; only the very first request after a start
does. A feed that is down is skipped, the others still show, and nothing at
all is a quiet empty banner rather than an error.

Feed XML is parsed with the standard library, which expands entities, so a
document declaring any (``<!DOCTYPE``/``<!ENTITY``) is refused before parsing,
and responses are capped in size. Only the fixed hosts below are ever asked.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import html
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

# (name shown to members, feed url, the zone its times are really in or None
# to trust the feed's own offsets). Football only: Sky's and BBC's feeds mix in
# other sports, which _is_football drops by URL.
#
# ESPN stamps every pubDate "EST" whatever the time really is -- its UK feed
# is London wall-clock time and its US one New York's, so those are read as
# written in the zone they actually are in, not the one they claim.
FEEDS = (
    ("BBC Sport", "https://feeds.bbci.co.uk/sport/football/rss.xml", None),
    ("The Guardian", "https://www.theguardian.com/football/rss", None),
    ("Sky Sports", "https://www.skysports.com/rss/12040", None),
    ("ESPN", "https://www.espn.co.uk/espn/rss/soccer/news", "Europe/London"),
)
TTL = 15 * 60.0
FAILED_TTL = 5 * 60.0  # nothing came back at all: try again sooner than usual
TIMEOUT = 8.0
MAX_BYTES = 2_000_000
PER_FEED = 12
MAX_AGE = dt.timedelta(days=3)
USER_AGENT = "SoccaIntel/1.0 (news headlines)"

_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Headline:
    title: str
    link: str
    source: str
    published_at: dt.datetime  # naive UTC


_lock = threading.Lock()
_cache: tuple[float, list[Headline]] | None = None  # (fetched at monotonic, headlines)
_refreshing = False


def _clean(text: str | None) -> str:
    return _SPACE.sub(" ", html.unescape(_TAGS.sub("", text or ""))).strip()


def _is_football(link: str) -> bool:
    return "/football" in link or "/soccer" in link


def _parse_time(raw: str | None, zone: str | None = None) -> dt.datetime | None:
    if not raw:
        return None
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if zone is not None:
        # The wall-clock reading as written, in the zone the feed really keeps.
        when = when.replace(tzinfo=ZoneInfo(zone))
    if when.tzinfo is not None:
        when = when.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return when


def parse_feed(xml_bytes: bytes, source: str, now: dt.datetime, zone: str | None = None) -> list[Headline]:
    """Headlines from one RSS document; ``[]`` for anything unreadable or unsafe."""

    head = xml_bytes[:2048].lower()
    if b"<!doctype" in head or b"<!entity" in xml_bytes[:20000].lower():
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    out: list[Headline] = []
    for item in root.iterfind("./channel/item"):
        title = _clean(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        published = _parse_time(item.findtext("pubDate"), zone)
        # Only an https link to the publisher: this becomes an <a href>.
        if not title or not link.startswith("https://") or not _is_football(link) or published is None:
            continue
        if now - published > MAX_AGE or published - now > dt.timedelta(hours=2):
            continue
        out.append(Headline(title[:200], link, source, published))
        if len(out) >= PER_FEED:
            break
    return out


def _fetch_one(name: str, url: str, zone: str | None, now: dt.datetime) -> list[Headline]:
    try:
        with requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, stream=True) as resp:
            if resp.status_code != 200:
                raise ValueError(f"HTTP {resp.status_code}")
            body = resp.raw.read(MAX_BYTES + 1, decode_content=True)
        if len(body) > MAX_BYTES:
            raise ValueError("response too large")
        return parse_feed(body, name, now, zone)
    except Exception as exc:  # noqa: BLE001 -- one bad feed must never take the others down
        logger.warning("news feed %s unavailable: %s", name, exc)
        return []


def _interleave(headlines) -> list[Headline]:
    """Newest first within each source, then one from each source in turn --
    so the first few the banner shows are not all from whoever published last."""

    by_source: dict[str, list[Headline]] = {}
    for h in sorted(headlines, key=lambda h: h.published_at, reverse=True):
        by_source.setdefault(h.source, []).append(h)
    queues = sorted(by_source.values(), key=lambda q: q[0].published_at, reverse=True)
    out: list[Headline] = []
    while any(queues):
        for queue in queues:
            if queue:
                out.append(queue.pop(0))
    return out


def _refresh(now: dt.datetime | None = None) -> list[Headline]:
    global _cache, _refreshing
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    try:
        with ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
            results = list(pool.map(lambda f: _fetch_one(f[0], f[1], f[2], now), FEEDS))
        merged: dict[str, Headline] = {}
        for headlines in results:
            for h in headlines:
                merged.setdefault(h.link.split("?")[0], h)
        ordered = _interleave(merged.values())
        with _lock:
            # Nothing came back (every feed down): keep the old headlines rather
            # than blank the banner, but retry soon.
            if ordered or _cache is None:
                _cache = (time.monotonic() if ordered else time.monotonic() - (TTL - FAILED_TTL), ordered)
            elif _cache is not None:
                _cache = (time.monotonic() - (TTL - FAILED_TTL), _cache[1])
            return _cache[1]
    finally:
        _refreshing = False


def headlines(limit: int = 12) -> list[Headline]:
    """The newest football headlines across the feeds, newest first. Served
    from cache; see the module docstring for when it refreshes."""

    global _refreshing
    with _lock:
        cached = _cache
        stale = cached is None or time.monotonic() - cached[0] > TTL
        start_background = stale and cached is not None and not _refreshing
        if start_background:
            _refreshing = True
    if cached is None:
        return _refresh()[:limit]
    if start_background:
        threading.Thread(target=_refresh, daemon=True).start()
    return cached[1][:limit]
