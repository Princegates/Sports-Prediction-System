#!/usr/bin/env python3
"""Create (or promote) the first superadmin account.

A superadmin account can only be made by promoting an existing one over the
API -- which is a chicken-and-egg problem for the very first admin. This
script bypasses that by talking to the database directly, the same way
Django's ``createsuperuser`` does. Run it once after setting up a fresh
database.

Interactive by default. On a hosted box there is often no TTY to type a
password into, so ``SUPERADMIN_PASSWORD`` in the environment is accepted as
an alternative -- that is what ``scripts/bootstrap.py`` uses.

Example:
    python scripts/create_superadmin.py --email admin@example.com --name "Site Admin"
    SUPERADMIN_PASSWORD=... python scripts/create_superadmin.py --email admin@example.com
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.auth.passwords import hash_password
from app.db.models import User
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default=None)
    args = parser.parse_args()

    init_db(engine)
    db = SessionLocal()
    try:
        email = args.email.strip().lower()
        existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

        if existing is not None:
            existing.role = "superadmin"
            existing.status = "active"
            db.commit()
            print(f"Promoted existing user {email!r} to superadmin (status set to active).")
            return

        # Env var first: a deploy shell frequently has no TTY, and
        # getpass would abort the whole bootstrap there.
        password = os.environ.get("SUPERADMIN_PASSWORD")
        if password:
            print("Using SUPERADMIN_PASSWORD from the environment.")
        else:
            password = getpass.getpass(f"Password for new superadmin {email!r}: ")
        if len(password) < 8:
            print("Password must be at least 8 characters.", file=sys.stderr)
            raise SystemExit(1)

        user = User(
            email=email,
            name=args.name or email,
            password_hash=hash_password(password),
            role="superadmin",
            status="active",
        )
        db.add(user)
        db.commit()
        print(f"Created superadmin {email!r}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
