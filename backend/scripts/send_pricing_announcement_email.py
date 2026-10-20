#!/usr/bin/env python3
"""Emails every active account the current price (Settings -> Access ->
"Price shown to visitors") -- a one-time broadcast for a new price or a
change to it, not a recurring nudge like the weekly-picks or access-
reminder emails.

Unlike send_access_reminder_email.py, the audience here is every active
account regardless of whether they already have a live access grant:
someone already subscribed still wants to know what their next code will
cost.

Two gates, both must be clear or nothing sends:

- Settings -> Email -> "Email members about the price"
  (pricing_announcement_email_enabled), off by default -- same reasoning
  as every other member-facing email here.
- Email itself has to be configured (SMTP or Resend).
- pricing_label itself has to be set (Settings -> Access) -- nothing to
  announce otherwise.

No per-user opt-out: a price change is account/product information, the
same footing as the welcome and password-changed emails, not a newsletter
preference.

    python scripts/send_pricing_announcement_email.py
    python scripts/send_pricing_announcement_email.py --dry-run
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from sqlalchemy import select

from app import app_settings, mailer
from app.db.migrate import init_db
from app.db.models import User
from app.db.session import SessionLocal, engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report who would be emailed without sending.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    init_db(engine)
    db = SessionLocal()
    try:
        values = app_settings.all_values(db)
        if not values.get("pricing_announcement_email_enabled", False):
            print("pricing_announcement_email_enabled is off (Settings -> Email) -- nothing sent.")
            return
        if not mailer.is_configured(db):
            print("Email isn't configured (Settings -> Email) -- nothing sent.")
            return

        price_label = app_settings.price_display(values)[0]
        if not price_label:
            print("pricing_label is blank (Settings -> Access) -- nothing to announce.")
            return

        recipients = list(db.execute(select(User).where(User.status == "active")).scalars())
        print(f"Announcing: {price_label!r}")
        print(f"Recipients: {len(recipients)} active account(s).")

        if not recipients:
            print("Nobody to email.")
            return

        site_url = str(values.get("public_site_url") or "") or None
        whatsapp = str(values.get("contact_whatsapp") or "") or None
        subject, body = mailer.pricing_announcement_message(price_label, site_url, whatsapp)

        if args.dry_run:
            print(f"\nSubject: {subject}\n\n{body}")
            print("Dry run -- nothing sent. Would email:")
            for user in recipients:
                print(f"    {user.email}")
            return

        sent = failed = 0
        for user in recipients:
            result = mailer.send_email(user.email, subject, body, db)
            if result.sent:
                sent += 1
            else:
                failed += 1
                print(f"    failed: {user.email} -- {result.error}", file=sys.stderr)

        print(f"\nSent {sent}, failed {failed}, of {len(recipients)}.")
        if failed and not sent:
            # Almost certainly a bad SMTP/Resend setting, not N individually
            # bad addresses -- worth a non-zero exit so this surfaces rather
            # than quietly reporting "0 sent" as if nothing was wrong.
            raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
