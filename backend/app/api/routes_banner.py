"""The news banner: the slides members see, and the sponsored items an admin
manages. See app.banner for what a slide is and app.news_feed for the headlines."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import banner
from app.api.deps import get_current_user, get_db, require_superadmin
from app.api.schemas import BannerItemIn, BannerItemOut, BannerItemUpdateIn, BannerOut, BannerSlideOut, MessageOut
from app.db.models import AuditLog, BannerItem, User
from app.timeutil import utcnow

router = APIRouter(prefix="/api/banner", tags=["banner"], dependencies=[Depends(get_current_user)])
admin_router = APIRouter(prefix="/api/admin/banner-items", tags=["admin"], dependencies=[Depends(require_superadmin)])


@router.get("", response_model=BannerOut)
def get_banner(db: Session = Depends(get_db)) -> BannerOut:
    return BannerOut(slides=[BannerSlideOut(**vars(s)) for s in banner.slides(db)])


def _count(db: Session, item_id: int, column) -> Response:
    """+1 on a sponsored item that is live right now; 404 for anything else, so
    a stale page can't keep counting an ad that has ended."""

    if not any(a.id == item_id for a in banner.live_ads(db)):
        raise HTTPException(status_code=404, detail="No such sponsored item")
    db.execute(update(BannerItem).where(BannerItem.id == item_id).values({column.key: column + 1}))
    db.commit()
    return Response(status_code=204)


@router.post("/ads/{item_id}/impression", status_code=204)
def ad_impression(item_id: int, db: Session = Depends(get_db)) -> Response:
    return _count(db, item_id, BannerItem.impressions)


@router.post("/ads/{item_id}/click", status_code=204)
def ad_click(item_id: int, db: Session = Depends(get_db)) -> Response:
    return _count(db, item_id, BannerItem.clicks)


# --- admin -------------------------------------------------------------------


def _out(item: BannerItem, now: dt.datetime) -> BannerItemOut:
    live = (
        item.active
        and (item.starts_at is None or item.starts_at <= now)
        and (item.ends_at is None or item.ends_at > now)
        and banner.valid_link(item.link_url)
    )
    return BannerItemOut(
        id=item.id, sponsor=item.sponsor, headline=item.headline, body=item.body, cta_label=item.cta_label,
        link_url=item.link_url, active=item.active, starts_at=item.starts_at, ends_at=item.ends_at,
        sort_order=item.sort_order, impressions=item.impressions, clicks=item.clicks,
        created_at=item.created_at, live=live,
    )


def _record(db: Session, admin: User, action: str, item: BannerItem) -> None:
    db.add(AuditLog(
        actor_user_id=admin.id, actor_email=admin.email, action=action,
        detail={"banner_item_id": item.id, "sponsor": item.sponsor},
    ))


def _checked_link(url: str) -> str:
    url = url.strip()
    if not banner.valid_link(url):
        raise HTTPException(status_code=422, detail="The link must be a full https:// address (no spaces).")
    return url


def _check_window(starts_at: dt.datetime | None, ends_at: dt.datetime | None) -> None:
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise HTTPException(status_code=422, detail="The end date must be after the start date.")


def _naive_utc(when: dt.datetime | None) -> dt.datetime | None:
    if when is not None and when.tzinfo is not None:
        return when.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return when


@admin_router.get("", response_model=list[BannerItemOut])
def list_items(db: Session = Depends(get_db)) -> list[BannerItemOut]:
    now = utcnow()
    rows = db.execute(select(BannerItem).order_by(BannerItem.sort_order, BannerItem.id.desc())).scalars()
    return [_out(r, now) for r in rows]


@admin_router.post("", response_model=BannerItemOut)
def create_item(payload: BannerItemIn, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> BannerItemOut:
    starts_at, ends_at = _naive_utc(payload.starts_at), _naive_utc(payload.ends_at)
    _check_window(starts_at, ends_at)
    item = BannerItem(
        sponsor=payload.sponsor.strip(), headline=payload.headline.strip(),
        body=(payload.body or "").strip() or None, cta_label=payload.cta_label.strip(),
        link_url=_checked_link(payload.link_url), active=payload.active, starts_at=starts_at, ends_at=ends_at,
        sort_order=payload.sort_order, created_by_user_id=admin.id,
    )
    db.add(item)
    db.flush()
    _record(db, admin, "banner_item.created", item)
    db.commit()
    db.refresh(item)
    return _out(item, utcnow())


def _get(item_id: int, db: Session) -> BannerItem:
    item = db.get(BannerItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Sponsored item not found")
    return item


@admin_router.patch("/{item_id}", response_model=BannerItemOut)
def update_item(
    item_id: int, payload: BannerItemUpdateIn, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)
) -> BannerItemOut:
    item = _get(item_id, db)
    data = payload.model_dump(exclude_unset=True)
    for field in ("sponsor", "headline", "cta_label"):
        if data.get(field) is not None:
            setattr(item, field, data[field].strip())
    if "body" in data:
        item.body = (data["body"] or "").strip() or None
    if data.get("link_url") is not None:
        item.link_url = _checked_link(data["link_url"])
    for field in ("active", "sort_order"):
        if data.get(field) is not None:
            setattr(item, field, data[field])
    if payload.clear_starts_at:
        item.starts_at = None
    elif payload.starts_at is not None:
        item.starts_at = _naive_utc(payload.starts_at)
    if payload.clear_ends_at:
        item.ends_at = None
    elif payload.ends_at is not None:
        item.ends_at = _naive_utc(payload.ends_at)
    _check_window(item.starts_at, item.ends_at)
    _record(db, admin, "banner_item.updated", item)
    db.commit()
    db.refresh(item)
    return _out(item, utcnow())


@admin_router.delete("/{item_id}", response_model=MessageOut)
def delete_item(item_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> MessageOut:
    item = _get(item_id, db)
    _record(db, admin, "banner_item.deleted", item)
    db.delete(item)
    db.commit()
    return MessageOut(message="Sponsored item deleted.")
