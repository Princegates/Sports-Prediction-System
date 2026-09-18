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
    print(f"\n=== Season access (current season would be {season}) ===")
    print("  Asking which seasons each league is readable for, rather than asking")
    print("  about one season -- that distinguishes a plan restriction from a bad query.\n")

    any_current = False
    for league_id, name in WANTED.items():
        try:
            # No season parameter: returns every season the plan can see.
            body, _ = call("leagues", f"id={league_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:<26} lookup failed ({type(exc).__name__})")
            continue

        if body.get("errors"):
            print(f"  {name:<26} error: {body['errors']}")
            continue

        found = body.get("response") or []
        if not found:
            print(f"  {name:<26} league id not visible at all")
            continue

        years = sorted({s.get("year") for s in (found[0].get("seasons") or []) if s.get("year")})
        if not years:
            print(f"  {name:<26} no seasons listed")
            continue

        has_current = season in years
        any_current = any_current or has_current
        span = f"{years[0]}-{years[-1]}" if len(years) > 1 else str(years[0])
        mark = "includes current" if has_current else f"CURRENT ({season}) MISSING"
        print(f"  {name:<26} seasons {span}  ({len(years)} total) — {mark}")

        if has_current:
            this = next(s for s in found[0]["seasons"] if s.get("year") == season)
            cov = this.get("coverage", {})
            fx = cov.get("fixtures", {})
            bits = [k for k, v in
                    (("events", fx.get("events")), ("lineups", fx.get("lineups")),
                     ("match stats", fx.get("statistics_fixtures")),
                     ("injuries", cov.get("injuries")), ("odds", cov.get("odds")))
                    if v]
            print(f"  {'':<26}   covers: {', '.join(bits) if bits else 'fixtures only'}")

    print()
    if any_current:
        print("  At least one competition covers the current season -- the integration is viable.")
    else:
        print("  No competition covers the current season on this key.")
        print("  That is a plan restriction, not a bug: the endpoints are all listed as")
        print("  available, but only for seasons the free tier includes.")

    print("\nPaste this output (it contains no key) and I'll build to whatever it says.")


if __name__ == "__main__":
    main()
