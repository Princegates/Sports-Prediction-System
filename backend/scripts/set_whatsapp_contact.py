#!/usr/bin/env python3
"""Sets the WhatsApp contact number shown wherever an account needs a Super
Admin for a code -- the welcome email, the locked-access page, and
registration (Settings -> Access -> "WhatsApp contact for access requests").

Accepts either shape:
  - A local Ghana mobile number: a leading 0 plus 9 digits (e.g.
    0553552693) -- normalized to international by dropping the 0 and
    prepending the 233 country code.
  - Already-international digits, no leading 0 (e.g. 233553552693) --
    used as-is.

The setting itself only ever stores the international, digits-only shape
(no spaces or punctuation) -- see app_settings.py's own spec and
mailer.format_whatsapp, which both expect exactly that.

    python scripts/set_whatsapp_contact.py 0553552693
    python scripts/set_whatsapp_contact.py 233553552693
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from app import app_settings
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine


def normalize(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("0") and len(digits) == 10:
        return "233" + digits[1:]
    return digits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("number", help="Local (0XXXXXXXXX) or international (233XXXXXXXXX) WhatsApp number.")
    args = parser.parse_args()

    digits = normalize(args.number)
    if len(digits) != 12:
        print(f"'{args.number}' normalized to '{digits}', which isn't the expected 3-digit country code + "
              "9-digit subscriber number shape. Not applying -- double check the number.", file=sys.stderr)
        raise SystemExit(1)

    init_db(engine)
    db = SessionLocal()
    try:
        before = app_settings.all_values(db)
        print(f"Before: contact_whatsapp={before.get('contact_whatsapp')!r}")

        app_settings.set_values(db, {"contact_whatsapp": digits})
        db.commit()

        after = app_settings.all_values(db)
        print(f"After:  contact_whatsapp={after.get('contact_whatsapp')!r}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
