"""Screenshots of won slips -- posted by an admin, or shared by any member
and reviewed first (see models.WinningSlip)."""

from __future__ import annotations

import base64
import binascii

from app.api.schemas import AdminWinningSlipOut, WinningSlipOut
from app.db.models import User, WinningSlip

# How many winning slips one member can share per day -- plenty for real
# wins, and a ceiling on anyone using it to flood the review queue.
MEMBER_DAILY_SUBMISSIONS = 5

# The browser shrinks a screenshot to ~1280px and re-encodes it before
# upload, which lands well under this; the cap is for anything that didn't.
MAX_WINNING_SLIP_BYTES = 2_000_000


class WinningSlipImageError(ValueError):
    pass


def _sniff(data: bytes) -> str | None:
    """The image type from its own first bytes -- never from what the
    upload claims, so nothing but a real JPEG/PNG/WebP is ever stored and
    served back under an image content type."""

    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_image(image_data: str) -> tuple[bytes, str]:
    raw = image_data.strip()
    if raw.startswith("data:"):
        _, _, raw = raw.partition(",")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WinningSlipImageError("That image couldn't be read -- try another file.") from exc
    if not data:
        raise WinningSlipImageError("That image is empty.")
    if len(data) > MAX_WINNING_SLIP_BYTES:
        raise WinningSlipImageError("That image is too large -- 2 MB at most.")
    content_type = _sniff(data)
    if content_type is None:
        raise WinningSlipImageError("Only JPEG, PNG or WebP images can be posted.")
    return data, content_type


def winning_slip_status(slip: WinningSlip) -> str:
    if slip.published:
        return "live"
    return "pending" if slip.pending_review else "not_shown"


def image_data_url(slip: WinningSlip) -> str:
    return f"data:{slip.content_type};base64,{base64.b64encode(slip.image).decode()}"


def winning_slip_to_schema(slip: WinningSlip) -> WinningSlipOut:
    return WinningSlipOut(**_fields(slip))


def admin_winning_slip_to_schema(slip: WinningSlip, submitter: User | None) -> AdminWinningSlipOut:
    return AdminWinningSlipOut(
        **_fields(slip),
        submitted_by_email=submitter.email if submitter else None,
        submitted_by_name=submitter.name if submitter else None,
    )


def _fields(slip: WinningSlip) -> dict:
    return dict(
        id=slip.id,
        caption=slip.caption or "",
        won_on=slip.won_on,
        image_path=f"/api/public/winning-slips/{slip.id}/image",
        width=slip.width,
        height=slip.height,
        published=slip.published,
        from_member=bool(slip.submitted_by_member),
        status=winning_slip_status(slip),
        created_at=slip.created_at,
    )
