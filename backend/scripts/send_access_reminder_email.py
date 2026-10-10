#!/usr/bin/env python3
"""Emails every active account that currently has no live access grant --
registered, but still on the free tier -- a nudge to get an access code.

Audience is computed fresh each run, not tracked: "active, no AccessGrant
row with status='active' and expires_at in the future, right now" -- the
same "new attention queue" app.api.routes_admin.overview() counts as
``users_without_access``. A superadmin is excluded even if they hold no
grant of their own, since require_active_access's own rule is "superadmin
or a live grant" -- they never need a code.

Two gates, both must be clear or nothing sends:

- Settings -> Email -> "Email members with no access yet"
  (access_reminder_email_enabled), off by default -- same reasoning as
  weekly_picks_email_enabled: a deployment that hasn't deliberately turned
  this on sends nothing.
- Email itself has to be configured (SMTP or Resend).

No per-user opt-out: this is about the recipient's own account state (you
don't have access), the same footing as the welcome and password-changed
emails, not a newsletter preference. Run on demand -- nothing schedules
this automatically, so re-running it is a deliberate choice each time, same
as issuing a code is.

    python scripts/send_access_reminder_email.py
    python scripts/send_access_reminder_email.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import exists, select

from app import app_settings, mailer
from app.db.migrate import init_db
from app.db.models import AccessGrant, User
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
        if not values.get("access_reminder_email_enabled", False):
            print("access_reminder_email_enabled is off (Settings -> Email) -- nothing sent.")
            return
        if not mailer.is_configured(db):
            print("Email isn't configured (Settings -> Email) -- nothing sent.")
            return

        now = dt.datetime.utcnow()
        has_live_grant = exists().where(
            AccessGrant.user_id == User.id, AccessGrant.status == "active", AccessGrant.expires_at > now
        )
        recipients = list(
            db.execute(
                select(User).where(User.status == "active", User.role != "superadmin", ~has_live_grant)
            ).scalars()
        )
        print(f"Recipients: {len(recipients)} active account(s) with no live access grant.")

        if not recipients:
            print("Nobody to remind.")
            return

        site_url = str(values.get("public_site_url") or "") or None
        whatsapp = str(values.get("contact_whatsapp") or "") or None
        subject, body = mailer.access_reminder_message(site_url, whatsapp)

        if args.dry_run:
            print("\nDry run -- nothing sent. Would email:")
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
