#!/usr/bin/env python3
"""Fail fast, and in plain English, on a malformed DATABASE_URL.

A bad connection string surfaces as a 60-line SQLAlchemy traceback ending in
"Could not parse SQLAlchemy URL from given URL string", which says nothing
about what is actually wrong with it. In practice it is almost always one of
a handful of paste accidents -- surrounding quotes, the whole psql command,
a missing scheme, the variable name left in front -- and each has an obvious
fix once named.

The secret itself is never printed. Every message here describes the *shape*
of the value, never its contents, so this is safe to run in CI logs.

    python scripts/check_database_url.py
"""

from __future__ import annotations

import os
import sys

# (test, explanation, fix) -- ordered so the most specific match wins.
DIAGNOSES = [
    (
        lambda v: v.lower().startswith(("psql ", "psql'", 'psql"')),
        "it starts with `psql`, so the whole command line was copied, not just the URL",
        "keep only the postgresql://... part",
    ),
    (
        lambda v: "=" in v.split("://")[0],
        "it contains the variable name (something like DATABASE_URL=postgresql://...)",
        "the value is just the URL -- the name goes in the field beside it",
    ),
    (
        lambda v: v[:1] in "\"'" or v[-1:] in "\"'",
        "it is wrapped in quotes, which become part of the value",
        "remove the surrounding quotes",
    ),
    (
        lambda v: "://" not in v,
        "it has no scheme",
        "it must begin with postgresql:// (or postgres://)",
    ),
    (
        lambda v: any(c.isspace() for c in v),
        "it contains a space or newline",
        "check for a line break from copying, or a stray trailing space",
    ),
    (
        lambda v: "[" in v or "]" in v,
        "it still contains square brackets, so a placeholder was left in",
        "replace [YOUR-PASSWORD] -- brackets included -- with the real password",
    ),
]


def diagnose(raw: str) -> tuple[str, str] | None:
    for test, explanation, fix in DIAGNOSES:
        try:
            if test(raw):
                return explanation, fix
        except Exception:  # noqa: BLE001 -- a probe must never be the thing that crashes
            continue
    return None


def main() -> None:
    raw = os.environ.get("DATABASE_URL", "")

    if not raw.strip():
        print("DATABASE_URL is not set.", file=sys.stderr)
        print(
            "  Add it under Settings > Secrets and variables > Actions, as a "
            "*repository* secret -- an environment secret is not visible to this "
            "workflow, which declares no environment.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sqlalchemy.engine import make_url

    from app.config import get_settings

    try:
        url = make_url(get_settings().normalized_database_url)
    except Exception as exc:  # noqa: BLE001 -- reporting beats re-raising here
        print(f"DATABASE_URL is not a usable connection string ({type(exc).__name__}).", file=sys.stderr)
        found = diagnose(raw.strip())
        if found:
            explanation, fix = found
            print(f"  Looks like {explanation}.", file=sys.stderr)
            print(f"  Fix: {fix}.", file=sys.stderr)
        else:
            print(
                "  Expected shape: postgresql://USER:PASSWORD@HOST:PORT/DATABASE\n"
                "  Special characters in the password must be percent-encoded "
                "(@ becomes %40), or the URL splits in the wrong place.",
                file=sys.stderr,
            )
        raise SystemExit(1)

    # Structure only -- never the password.
    print("DATABASE_URL parses:")
    print(f"  driver   {url.drivername}")
    print(f"  user     {url.username or '(none)'}")
    print(f"  host     {url.host or '(none)'}")
    print(f"  port     {url.port or '(default)'}")
    print(f"  database {url.database or '(none)'}")

    # Parsing is necessary but not sufficient: the two worst mistakes both
    # produce a perfectly valid URL that points somewhere wrong.
    problems: list[str] = []

    if url.host and "@" in url.host:
        problems.append(
            "the host contains an '@', so an unencoded '@' in the password split "
            "the URL in the wrong place -- encode it as %40"
        )

    if any("[" in (part or "") or "]" in (part or "") for part in (url.password, url.username, url.host)):
        problems.append(
            "it still contains square brackets, so a placeholder was never replaced "
            "-- swap [YOUR-PASSWORD], brackets included, for the real password"
        )

    if problems:
        print(file=sys.stderr)
        for problem in problems:
            print(f"  PROBLEM: {problem}.", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
