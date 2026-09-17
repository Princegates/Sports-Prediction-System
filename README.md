# AI Football Prediction & Analytics System

A working implementation of the core engine described in the project's System
Requirements Specification: an ensemble prediction system that turns historical
football data into calibrated probabilities across multiple markets, and picks
the single **Global Most-Likely Outcome** for each match.

## Zero-cost by design

Every data source and every piece of infrastructure in this build is free:

| Need                     | Source                                                                 | Cost |
|---------------------------|-------------------------------------------------------------------------|------|
| Historical results (10+ seasons, 20+ leagues) | [football-data.co.uk](https://www.football-data.co.uk/data.php) CSV downloads — no signup, no key | Free |
| Upcoming fixtures         | [TheSportsDB](https://www.thesportsdb.com/api.php) public test key (`3`) | Free |
| Database                  | SQLite by default (file on disk); swap in Postgres via `DATABASE_URL` if you want | Free (self-hosted) |
| ML / stats                | scikit-learn, numpy, pandas (all open-source, run locally)              | Free |
| Backend                   | FastAPI + Uvicorn                                                       | Free |
| Frontend                  | React + Vite                                                             | Free |

No API keys that require a credit card are used anywhere. `TheSportsDB` test
key `3` is rate-limited but sufficient for fixture discovery; if you later
register your own free key, drop it into `.env` and nothing else changes.
There is no dependency on a paid odds/livescore feed — the system is
market-independent (it never looks at bookmaker odds to form its own
predictions), matching the spec's design goal.

Live in-play data (second-by-second stats from a paid provider) is the one
piece of the SRS that genuinely has no free equivalent at professional
quality. Instead of skipping it, the **live engine is fully built** but
pluggable: `POST /api/matches/{id}/live-event` lets you (or a free/manual feed
you wire up later) push score/card/substitution events, and the in-play
model recalculates the Global Most-Likely Outcome immediately, exactly per
spec section 29-32. Swap in a paid provider later with zero changes to the
prediction logic.

## What's implemented

- **Data pipeline** (`backend/app/data`): imports historical match results
  from football-data.co.uk into the database; pulls upcoming fixtures from
  TheSportsDB.
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

# 1. Import a few seasons of free historical data (no key needed)
python scripts/fetch_historical_data.py --league E0 --seasons 2122 2223 2324 2425

# 2. Train the models + run a temporal backtest
python scripts/backtest.py --league E0

# 3. Pull upcoming fixtures (free TheSportsDB test key by default) and generate predictions
python scripts/build_predictions.py --league "English Premier League"

# 4. Start the API
uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
npm run dev   # set VITE_API_URL if the backend isn't on localhost:8000
```

### Or with Docker

```bash
docker compose up --build
# then, one time, run the same three data/training steps inside the backend container:
docker compose exec backend python scripts/fetch_historical_data.py --league E0 --seasons 2223 2324 2425
docker compose exec backend python scripts/backtest.py --league E0
docker compose exec backend python scripts/build_predictions.py --league "English Premier League"
```

Frontend on `:4173`, API on `:8000`. Both images are plain open-source
Python/Node base images -- no paid registry, no paid compute required beyond
wherever you choose to run Docker itself.

## Responsible-prediction note

Per spec section 62, nothing in this system presents a probability as a
guarantee. Every prediction response includes `confidence`, `data_quality`,
and `model_agreement` alongside the probability itself, and the frontend
never uses language like "guaranteed" — only "Model Probability: X%".
