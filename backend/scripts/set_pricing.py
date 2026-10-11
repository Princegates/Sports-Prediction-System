#!/usr/bin/env python3
"""Sets the price shown to visitors (Settings -> Access -> "Price shown to
visitors") and, optionally, turns on the one-time pricing-announcement email
(Settings -> Email -> "Email members about the price") so it's ready for
scripts/send_pricing_announcement_email.py to broadcast next.

Prints the before/after values so the dispatch log itself confirms it
worked -- there's no admin UI access from this operational path.

    python scripts/set_pricing.py "GHC 20 / week"
    python scripts/set_pricing.py "GHC 20 / week" --enable-announcement-email
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from app import app_settings
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("price", help='The price label to show, e.g. "GHC 20 / week".')
    parser.add_argument(
        "--enable-announcement-email",
        action="store_true",
        help="Also turn on pricing_announcement_email_enabled (Settings -> Email).",
    )
    args = parser.parse_args()

    init_db(engine)
    db = SessionLocal()
    try:
        before = app_settings.all_values(db)
        print(
            f"Before: pricing_label={before.get('pricing_label')!r} "
            f"pricing_announcement_email_enabled={before.get('pricing_announcement_email_enabled')!r}"
        )

        updates: dict[str, object] = {"pricing_label": args.price}
        if args.enable_announcement_email:
            updates["pricing_announcement_email_enabled"] = True

        app_settings.set_values(db, updates)
        db.commit()

        after = app_settings.all_values(db)
        print(
            f"After:  pricing_label={after.get('pricing_label')!r} "
            f"pricing_announcement_email_enabled={after.get('pricing_announcement_email_enabled')!r}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
