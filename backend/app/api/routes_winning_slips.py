"""Members sharing their own winning slips (Share a win).

Any signed-in account can submit a screenshot (or a ticket PDF the browser
has already converted -- see frontend lib/slipFromPdf). It never shows
publicly straight away: it waits in the admin review queue
(routes_admin's winning-slips endpoints) until approved. A member sees and
can delete only their own submissions.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.rate_limit import enforce_key
from app.api.schemas import MessageOut, WinningSlipImageDataOut, WinningSlipIn, WinningSlipOut
from app.db.models import AuditLog, User, WinningSlip
from app.winning_slips import (
    MEMBER_DAILY_SUBMISSIONS,
    WinningSlipImageError,
    decode_image,
    image_data_url,
    winning_slip_to_schema,
)

router = APIRouter(prefix="/api/winning-slips", tags=["winning-slips"])


@router.post("", response_model=WinningSlipOut)
def share_winning_slip(
    payload: WinningSlipIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> WinningSlipOut:
    enforce_key(f"winning_slip-user:{user.id}", MEMBER_DAILY_SUBMISSIONS, 24 * 3600)
    try:
        image, content_type = decode_image(payload.image_data)
    except WinningSlipImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # A superadmin's own share needs no review; anyone else's does.
    is_admin = user.role == "superadmin"
    slip = WinningSlip(
        image=image, content_type=content_type, width=payload.width, height=payload.height,
        caption=payload.caption.strip(), won_on=payload.won_on,
        published=is_admin, pending_review=not is_admin, submitted_by_member=not is_admin,
        created_by_user_id=user.id,
    )
    db.add(slip)
    db.flush()
    db.add(AuditLog(
        actor_user_id=user.id, actor_email=user.email, action="winning_slip.shared",
        target_user_id=user.id, detail={"winning_slip_id": slip.id, "bytes": len(image)},
    ))
    db.commit()
    db.refresh(slip)
    return winning_slip_to_schema(slip)


@router.get("/mine", response_model=list[WinningSlipOut])
def my_winning_slips(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[WinningSlipOut]:
    rows = db.execute(
        select(WinningSlip)
        .where(WinningSlip.created_by_user_id == user.id)
        .order_by(WinningSlip.created_at.desc(), WinningSlip.id.desc())
    ).scalars()
    return [winning_slip_to_schema(s) for s in rows]


def _own(slip_id: int, user: User, db: Session) -> WinningSlip:
    slip = db.get(WinningSlip, slip_id)
    if slip is None or slip.created_by_user_id != user.id:
        raise HTTPException(status_code=404, detail="Winning slip not found")
    return slip


@router.get("/{slip_id}/image-data", response_model=WinningSlipImageDataOut)
def my_winning_slip_image(
    slip_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> WinningSlipImageDataOut:
    """The image of one of your own submissions, even before it's approved."""

    return WinningSlipImageDataOut(data_url=image_data_url(_own(slip_id, user, db)))


@router.delete("/{slip_id}", response_model=MessageOut)
def delete_my_winning_slip(
    slip_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> MessageOut:
    db.delete(_own(slip_id, user, db))
    db.add(AuditLog(
        actor_user_id=user.id, actor_email=user.email, action="winning_slip.withdrawn",
        target_user_id=user.id, detail={"winning_slip_id": slip_id},
    ))
    db.commit()
    return MessageOut(message="Removed.")
