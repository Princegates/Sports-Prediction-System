# AI Football Prediction & Analytics System

A working implementation of the core engine described in the project's System
Requirements Specification: an ensemble prediction system that turns historical
football data into calibrated probabilities across multiple markets, and picks
the single **Global Most-Likely Outcome** for each match.

Plus a full public website, a Super-Admin-gated membership flow, and a grounded
AI chat assistant. See **[ROADMAP.md](ROADMAP.md)** for a candid audit of what
works, what doesn't, and what it would take to make this world-class.

## The site at a glance

| Route | Access | What it is |
|---|---|---|
| `/` | Public | Welcome page — live corpus stats, measured accuracy, how it works, FAQ |
| `/how-it-works` | Public | Full methodology, including what the system *can't* do |
| `/responsible` | Public | Responsible-use guidance and helplines |
| `/register` | Public | Request access — creates a **pending** account |
| `/account-status` | Public | Check where a pending application stands |
| `/login` | Public | Sign in (refused until approved) |
| `/app` | Members | Dashboard of today's fixtures |
| `/app/match/:id` | Members | Full probability table, correct-score grid, AI explanation |
| `/app/predictions`, `/app/live`, `/app/teams/:id`, `/app/profile` | Members | Predictions table, in-play, team pages, personal profile |
| `/app/admin` | Super Admin | Approval queue, account management, audit log |

Every figure on the public pages is read live from `/api/public/*`. A deployment
with no data imported says so, rather than displaying invented numbers.

## Membership requires Super Admin approval

This is enforced at the router level, not just in the UI:

1. A visitor registers at `/register`, optionally submitting a payment reference.
   The account is created with `status="pending"`.
2. `get_current_user` rejects any account whose status isn't `active`, and it
   guards every router except `/api/auth/*` and `/api/public/*`. A pending
   account therefore has **no** access to any prediction, team, match or chat
   endpoint — there is no partial access and no trial tier.
3. A Super Admin reviews the queue at `/app/admin`, confirms the payment
   reference out of band, and approves. The approval is written to an
   append-only audit log along with who did it and when.
4. The member can now sign in. Their theme, colour profile and viewing history
   are stored on the account, so they follow them across devices.

Suspension is reversible (`/reinstate`), self-suspension and self-demotion are
refused, and the system will not let you remove the last Super Admin.

Create the first Super Admin — which can't go through the approval flow, for
obvious reasons — with:

```bash
python scripts/create_superadmin.py --email you@example.com --name "Site Admin"
```

## The AI chat assistant

A floating chat dock on every member page (⌘J / Ctrl-J), plus
`POST /api/chat/message` and an SSE streaming endpoint.

It is **grounded, not generative**. It parses the question, resolves the teams
against the `teams` table, fetches the relevant rows, and composes a reply from
them. That ordering is what makes a fabricated statistic structurally
impossible rather than merely unlikely — and it's why the assistant answers
"no backtest has been recorded" instead of inventing a plausible hit rate.

It handles fixture predictions, reasoning ("why is this favored?", "what are the
risks?"), team form, head-to-head, best picks, live status, and questions about
the model's own accuracy and methodology. Answers carry clickable citations to
the match or team they came from. On a match page it picks up that match as
context, so follow-ups don't need to re-name the teams. Anything it can't
classify gets an honest "I don't know", never a guess.

Questions about guaranteed wins or recovering losses are routed to a
responsible-use answer with helpline details, ahead of every other pattern.

An **optional** phrasing-only rewriter can be pointed at a local LLM (Ollama
etc.) via `ASSISTANT_LLM_ENABLED`. It's off by default, forbidden from
introducing any number not already in the grounded text, and falls back to that
text on any failure — so the assistant costs nothing to run.

## Zero-cost by design

Every data source and every piece of infrastructure in this build is free.
There are two interchangeable historical/fixture providers -- use whichever
one your network can actually reach (some sandboxes/corporate networks only
allow GitHub, in which case openfootball is the one that works):

| Need                     | Source                                                                 | Cost |
|---------------------------|-------------------------------------------------------------------------|------|
| Historical results + real fixtures, one source | [openfootball/football.json](https://github.com/openfootball/football.json) on GitHub — no signup, no key, one JSON file per league/season with both played and not-yet-played matches | Free |
| Historical results (10+ seasons, 20+ leagues) — alternative | [football-data.co.uk](https://www.football-data.co.uk/data.php) CSV downloads — no signup, no key | Free |
| Upcoming fixtures — alternative | [TheSportsDB](https://www.thesportsdb.com/api.php) — **currently non-functional**: every league-listing method is capped at a handful of results on the free tier as of this writing, so it can't reliably find a league by name any more. Kept in the codebase in case they loosen this. | Free (when it works) |
| Database                  | SQLite by default (file on disk); swap in Postgres via `DATABASE_URL` if you want | Free (self-hosted) |
| ML / stats                | scikit-learn, numpy, pandas (all open-source, run locally)              | Free |
| Backend                   | FastAPI + Uvicorn                                                       | Free |
| Frontend                  | React + Vite                                                             | Free |

No API keys that require a credit card are used anywhere. There is no
dependency on a paid odds/livescore feed — the system is market-independent
(it never looks at bookmaker odds to form its own predictions), matching the
spec's design goal.

This has been run end-to-end on real data: real Premier League and La Liga
results back to 2019, real Elo/Poisson/Gradient-Boosting training, a real
temporal backtest, and real predictions for actual scheduled fixtures pulled
straight from `openfootball/football.json` (including a same-day match, so
the "today's matches" dashboard is showing a genuine fixture, not a
placeholder).

Live in-play data (second-by-second stats from a paid provider) is the one
piece of the SRS that genuinely has no free equivalent at professional
quality. Instead of skipping it, the **live engine is fully built** but
pluggable: `POST /api/matches/{id}/live-event` lets you (or a free/manual feed
you wire up later) push score/card/substitution events, and the in-play
model recalculates the Global Most-Likely Outcome immediately, exactly per
spec section 29-32. Swap in a paid provider later with zero changes to the
prediction logic.

## What's implemented

- **Data pipeline** (`backend/app/data`): three interchangeable providers --
  `openfootball.py` (recommended: one JSON file per league/season with both
  historical results and genuine not-yet-played fixtures), plus
  `football_data_co_uk.py` (historical results only) and `thesportsdb.py`
  (fixture lookup only) as alternatives.
- **Elo model** (`prediction_models/elo.py`): full match-by-match Elo rating
  system with home advantage and a margin-of-victory multiplier, converted to
  calibrated 1X2 probabilities via a fitted logistic model on Elo difference.
- **Poisson goal model** (`prediction_models/poisson_model.py`): attack/defense
  strength ratings per team (home/away split) with a Dixon-Coles low-score
  correction, producing the full score-matrix, 1X2, Over/Under 0.5-4.5, BTTS,
  and Correct Score markets.
- **Gradient boosting model** (`prediction_models/ml_model.py`): scikit-learn
  model trained on engineered features (form, goal differentials, rest days,
  Elo gap) for 1X2, Over 2.5 and BTTS.
- **Ensemble + calibration** (`prediction_models/ensemble.py`,
  `calibration.py`): weighted blend of the three models above, then isotonic
  regression calibration fit on a held-out validation split.
- **Global Most-Likely Outcome Engine** (`outcomes/`): maintains an Outcome
  Registry (market, selection, mutually-exclusive group, minimum data
  requirement) and selects `argmax(probability)` only among outcomes whose
  data-quality gate passes — implementing spec sections 26-27 including the
  "mutually exclusive vs not" distinction.
- **Explainable AI** (`explain.py`): generates the positive/negative factor
  list (spec section 36) from real feature deltas, not a template.
- **Confidence & data-quality scoring** (`quality.py`): spec sections 33-35.
- **Live engine**: `live_predictions` table + recalculation triggered by
  goal/red-card/substitution/etc. events (spec sections 29-32).
- **Temporal backtesting** (`scripts/backtest.py`): train/validation/test are
  split by date (never shuffled), reporting accuracy, log loss, Brier score,
  and a calibration table (spec sections 40-41).
- **REST API** (`backend/app/api`): matches, predictions, today's card,
  high-confidence filter, live events — matching spec section 54-55.
- **Frontend** (`frontend/`): dashboard of today's matches with the
  Most-Likely-Outcome badge, and a match detail page with the full
  probability table, correct-score grid, and AI explanation — responsive for
  desktop/tablet/phone.

## Security notes before deploying

Two things to do before this touches a network:

1. **Set `SECRET_KEY`.** Session tokens are HMAC-signed with it, and the default
   is published in this repository — anyone who has read it can forge a token
   for any account, including a Super Admin. The app logs a warning at startup
   while the default is in place. Generate one with
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
2. **Set `CORS_ALLOW_ORIGINS`** to your real origins instead of `*`.

Login, registration and chat are rate-limited per client IP. That limiter is
in-process, so limits are per-worker and reset on restart — fine for a single
uvicorn worker, and `app/api/rate_limit.py` documents the Redis upgrade path
for anything larger.

## What's intentionally out of scope for this pass

Building all 65 SRS sections (player-level NLP news ingestion, referee/
weather/tactical sub-models, WebSocket push, notification engine, full RBAC)
in one pass would mean a lot of shallow, untested code. The phases above give
you a real, calibrated, backtested prediction engine end-to-end. The
remaining phases slot into the existing architecture:

- **Player/lineup/news intelligence** — add a `PlayerImpactModel` next to
  `ml_model.py` and register it as another weighted member of the ensemble;
  the Outcome Registry and API already support recalculation triggers.
- **Tactical/referee/weather/context engines** — same pattern: each is a
  feature source that feeds `ml_model.py`, not a rearchitecture.
- **WebSocket push / notifications** — the live-event endpoint already
  recomputes and stores the new `live_predictions` row; broadcasting it over
  a WebSocket instead of polling is an additive change to `api/routes_live.py`.

## Running it

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# 1. Import real historical + current-season data (played AND upcoming
#    fixtures, in one shot -- no key, works anywhere that can reach GitHub)
python scripts/fetch_openfootball_data.py --league "English Premier League" \
    --seasons 2019-20 2020-21 2021-22 2022-23 2023-24 2024-25 2025-26 2026-27

# 2. Train the models + run a temporal backtest (accuracy/log-loss/Brier/calibration)
python scripts/backtest.py --league-name "English Premier League"

# 3. Generate predictions for the real scheduled fixtures step 1 already imported
python scripts/generate_predictions.py --league "English Premier League" --days-ahead 10

# 4. Start the API
uvicorn app.main:app --reload
```

Prefer football-data.co.uk instead for historical data (e.g. GitHub isn't
reachable but that is)? Use `scripts/fetch_historical_data.py` (`--league E0
--seasons 2223 2324 2425`, football-data.co.uk's own season codes) in place
of step 1, and `--league E0` instead of `--league-name "..."` in step 2 --
everything downstream (models, API, frontend) is identical either way.
`scripts/build_predictions.py` (the TheSportsDB-backed fixture fetcher) is
currently unreliable -- see the table above -- so step 3 stays on
`generate_predictions.py` regardless of which historical source you used.

```bash
cd frontend
npm install
npm run dev   # set VITE_API_URL if the backend isn't on localhost:8000
```

### Or with Docker

```bash
docker compose up --build
# then, one time, run the same data/training steps inside the backend container:
docker compose exec backend python scripts/fetch_openfootball_data.py --league "English Premier League" --seasons 2022-23 2023-24 2024-25 2025-26 2026-27
docker compose exec backend python scripts/backtest.py --league-name "English Premier League"
docker compose exec backend python scripts/generate_predictions.py --league "English Premier League" --days-ahead 10
```

Frontend on `:4173`, API on `:8000`. Both images are plain open-source
Python/Node base images -- no paid registry, no paid compute required beyond
wherever you choose to run Docker itself.

## Responsible-prediction note

Per spec section 62, nothing in this system presents a probability as a
guarantee. Every prediction response includes `confidence`, `data_quality`,
and `model_agreement` alongside the probability itself, and the frontend
never uses language like "guaranteed" — only "Model Probability: X%".
