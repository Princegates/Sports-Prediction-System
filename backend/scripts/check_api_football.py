#!/usr/bin/env python3
"""Report what an API-Sports (API-Football) key can actually reach.

Run this before anything is built on top of the key. The plan tier decides
the shape of the integration -- a 100-request daily budget means caching
aggressively and fetching lineups only for matches about to kick off, while a
larger plan allows a much simpler design. Guessing wrong means either wasting
the quota or building caution nobody needed.

    export API_FOOTBALL_KEY=...        # macOS / Linux
    $env:API_FOOTBALL_KEY = "..."      # Windows PowerShell

    python scripts/check_api_football.py

It prints your plan, your remaining requests, which of the leagues this
project uses are available, and whether the Champions League is covered. It
never prints the key.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

# API-Sports direct. Through RapidAPI the host and header differ; set
# API_FOOTBALL_HOST to the RapidAPI host and this switches automatically.
DIRECT_HOST = "v3.football.api-sports.io"
HOST = os.environ.get("API_FOOTBALL_HOST", DIRECT_HOST).strip()
KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()

# What this project already predicts, by API-Football's own league ids.
WANTED = {
    39: "English Premier League",
    140: "Spanish La Liga",
    135: "Italian Serie A",
    78: "German Bundesliga",
    61: "French Ligue 1",
    2: "UEFA Champions League",
    3: "UEFA Europa League",
}


def call(path: str, params: str = "") -> tuple[dict, dict]:
    url = f"https://{HOST}/{path}" + (f"?{params}" if params else "")
    headers = (
        {"x-rapidapi-key": KEY, "x-rapidapi-host": HOST}
        if "rapidapi" in HOST
        else {"x-apisports-key": KEY}
    )
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode()), dict(response.headers)


def main() -> None:
    if not KEY:
        print("API_FOOTBALL_KEY is not set.", file=sys.stderr)
        print("  macOS/Linux : export API_FOOTBALL_KEY=your-key", file=sys.stderr)
        print("  PowerShell  : $env:API_FOOTBALL_KEY = \"your-key\"", file=sys.stderr)
        raise SystemExit(1)

    print(f"Host: {HOST}")
    print(f"Key : {'*' * 8}{KEY[-4:]}  (last 4 shown so you can tell keys apart)\n")

    try:
        status, headers = call("status")
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} from /status -- the key was rejected or the host is wrong.", file=sys.stderr)
        print(f"  body: {exc.read()[:300].decode(errors='replace')}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not reach {HOST}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)

    errors = status.get("errors")
    if errors:
        print(f"The API returned errors: {errors}", file=sys.stderr)
        raise SystemExit(1)

    response = status.get("response", {})
    account = response.get("account", {})
    subscription = response.get("subscription", {})
    requests_info = response.get("requests", {})

    print("=== Plan ===")
    print(f"  account       : {account.get('firstname', '')} {account.get('lastname', '')}".rstrip())
    print(f"  plan          : {subscription.get('plan', 'unknown')}")
    print(f"  active        : {subscription.get('active')}")
    print(f"  ends          : {subscription.get('end', 'n/a')}")
    print(f"  requests today: {requests_info.get('current')} of {requests_info.get('limit_day')}")

    remaining = headers.get("x-ratelimit-requests-remaining")
    per_minute = headers.get("X-RateLimit-Limit") or headers.get("x-ratelimit-limit")
    if remaining is not None:
        print(f"  remaining     : {remaining}")
    if per_minute is not None:
        print(f"  per-minute cap: {per_minute}")

    season = dt.date.today().year if dt.date.today().month >= 7 else dt.date.today().year - 1
    print(f"\n=== Coverage for season {season} ===")

    for league_id, name in WANTED.items():
        try:
            body, _ = call("leagues", f"id={league_id}&season={season}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:<26} lookup failed ({type(exc).__name__})")
            continue

        found = body.get("response") or []
        if not found:
            print(f"  {name:<26} NOT available on this plan/season")
            continue

        seasons = found[0].get("seasons") or []
        this_season = next((s for s in seasons if s.get("year") == season), None)
        coverage = (this_season or {}).get("coverage", {})
        fixtures = coverage.get("fixtures", {})
        bits = []
        if fixtures.get("events"):
            bits.append("events")
        if fixtures.get("lineups"):
            bits.append("lineups")
        if fixtures.get("statistics_fixtures"):
            bits.append("match stats")
        if coverage.get("injuries"):
            bits.append("injuries")
        if coverage.get("odds"):
            bits.append("odds")
        print(f"  {name:<26} available — {', '.join(bits) if bits else 'fixtures only'}")

    print("\nPaste this output (it contains no key) and I'll build to whatever it says.")


if __name__ == "__main__":
    main()
