#!/usr/bin/env python3
"""Emails every opted-in active account once the week's system accumulators
are up -- a short nudge back to the Dashboard, never the picks themselves.

Runs after scripts/generate_weekly_picks.py and
scripts/generate_high_risk_slips.py in the weekly retrain (same workflow
step, same ``weekly_picks: skip`` escape hatch a midweek manual retrain
already uses to avoid swapping out picks members may have booked against --
skipping that step skips this one too, for the same reason: nothing new to
announce).

Two gates, both must be clear or nothing sends:

- Settings -> Email -> "Email members about new weekly picks"
  (weekly_picks_email_enabled), off by default. A deployment that hasn't
  deliberately turned this on sends nothing, same as every other optional
  feature here.
- Email itself has to be configured (SMTP or Resend) -- this reads the same
  settings every other mailer.send_email caller does.

Not idempotent by design, not by oversight: running this twice re-sends to
everyone. The ordinary cadence (the weekly cron) only runs it once a week;
an admin choosing to manually retrain *without* weekly_picks: skip is
choosing to republish the week's picks, and a courtesy re-announcement is
the right side of that choice to land on, not a bug to guard against.

    python scripts/send_weekly_picks_email.py
    python scripts/send_weekly_picks_email.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app import app_settings, mailer
from app.db.migrate import init_db
from app.db.models import AdminPick, User
from app.db.session import SessionLocal, engine

SOURCE_PREFIX = "system_weekly_"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report who/what would be sent without sending.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    init_db(engine)
    db = SessionLocal()
    try:
        values = app_settings.all_values(db)
        if not values.get("weekly_picks_email_enabled", False):
            print("weekly_picks_email_enabled is off (Settings -> Email) -- nothing sent.")
            return
        if not mailer.is_configured(db):
            print("Email isn't configured (Settings -> Email) -- nothing sent.")
            return

        labels = list(
            db.execute(
                select(AdminPick.label)
                .where(AdminPick.source.like(f"{SOURCE_PREFIX}%"), AdminPick.result == "pending")
                .order_by(AdminPick.source)
            ).scalars()
        )
        if not labels:
            print("No live weekly picks found (source LIKE 'system_weekly_%', still pending) -- nothing to announce.")
            return

        print(f"This week's picks ({len(labels)}):")
        for label in labels:
            print(f"    {label}")

        site_url = str(values.get("public_site_url") or "") or None
        subject, body = mailer.weekly_picks_message(labels, site_url)

        recipients = list(
            db.execute(
                select(User).where(User.status == "active", User.notify_weekly_picks.is_(True))
            ).scalars()
        )
        print(f"\nRecipients: {len(recipients)} active account(s) opted in.")

        if args.dry_run:
            print("\nDry run -- nothing sent.")
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
            # Every single send failed -- almost certainly a bad SMTP/Resend
            # setting rather than N individually bad addresses, worth a
            # non-zero exit so a scheduled run surfaces it instead of
            # quietly reporting "0 sent" as if nothing was wrong.
            raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
