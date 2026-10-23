"""The news banner's slides: football headlines, with sponsored items mixed in.

One list for the whole banner, so adverts are not a separate bolt-on: an ad is
a slide like any other, in the same place, just always labelled "Sponsored"
and always carrying the sponsor's name. They are text only (see
``BannerItem``) and a link out; nothing from a third party is ever rendered as
markup.

Sponsored slides are spaced through the headlines -- one after every
``AD_EVERY`` news slides -- rather than clustered, so a banner with several
live ads still reads as news with the odd sponsor, not the reverse.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import news_feed
from app.db.models import BannerItem
from app.timeutil import utcnow

AD_EVERY = 3
MAX_SLIDES = 14


@dataclass(frozen=True)
class Slide:
    id: str  # "news:<hash>" or "ad:<banner item id>"
    kind: str  # "news" | "ad"
    headline: str
    label: str  # the publisher, or the sponsor's name
    url: str
    body: str | None = None
    cta: str | None = None
    published_at: dt.datetime | None = None


def valid_link(url: str) -> bool:
    """An https link with a host and no whitespace -- the only kind that may
    become an ``<a href>``; ``javascript:`` and friends never get this far."""

    if not url or len(url) > 500 or any(c.isspace() for c in url):
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" and bool(parts.hostname) and "." in parts.hostname


def live_ads(db: Session, now: dt.datetime | None = None) -> list[BannerItem]:
    """Sponsored items switched on and inside their dates, in display order."""

    now = now or utcnow()
    return list(db.execute(
        select(BannerItem)
        .where(
            BannerItem.active.is_(True),
            or_(BannerItem.starts_at.is_(None), BannerItem.starts_at <= now),
            or_(BannerItem.ends_at.is_(None), BannerItem.ends_at > now),
        )
        .order_by(BannerItem.sort_order, BannerItem.id)
    ).scalars())


def slides(db: Session, now: dt.datetime | None = None) -> list[Slide]:
    now = now or utcnow()
    news = [
        Slide(
            id="news:" + hashlib.md5(h.link.split("?")[0].encode()).hexdigest()[:10], kind="news", headline=h.title,
            label=h.source, url=h.link, published_at=h.published_at,
        )
        for h in news_feed.headlines(MAX_SLIDES)
    ]
    ads = [
        Slide(
            id=f"ad:{a.id}", kind="ad", headline=a.headline, label=a.sponsor, url=a.link_url,
            body=a.body, cta=a.cta_label,
        )
        for a in live_ads(db, now)
        if valid_link(a.link_url)  # re-checked at serve time, not only when saved
    ]
    out: list[Slide] = []
    ad_iter = iter(ads)
    for i, item in enumerate(news, start=1):
        out.append(item)
        if i % AD_EVERY == 0 and (ad := next(ad_iter, None)) is not None:
            out.append(ad)
    out.extend(ad_iter if not news else [])  # no headlines at all: the ads still run
    return out[:MAX_SLIDES]
