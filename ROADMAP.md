# Audit & Roadmap

Where this system stands today, what's genuinely missing, and what it would take to
make it world-class — with the cost of each item stated honestly.

Written after a full read of the codebase and live runs against real data — five
European leagues, 9,033 completed matches across 2021-22 to 2026-27, with real
temporal backtests per league.

---

## Part 1 — Audit

### What's already strong

These are real, working, and better than most things calling themselves an AI
prediction system:

| Area | State | Why it's good |
|---|---|---|
| **Ensemble modelling** | Elo + Dixon-Coles Poisson + gradient boosting, weighted blend | Three models that fail differently. Most products are one Poisson model with a marketing page. |
| **Calibration** | Isotonic regression fitted on a held-out validation split | This is the step almost everyone skips. It's what makes "70%" mean 70%. |
| **Leakage discipline** | Every feature computed as-of kickoff; splits by date, never shuffled | The single most common way prediction systems lie to themselves, avoided by construction. |
| **Outcome registry** | 18 markets scored per match, with mutually-exclusive grouping and per-outcome data gates | The gate is the important part: thin data can't produce a confident-looking call. |
| **Explainability** | Positive/negative factors from real feature deltas, plus per-model votes | Not a template. Traces to actual numbers. |
| **Honest uncertainty** | Confidence band + data-quality score + model-agreement score on every prediction | Three independent signals, not one vague "confidence: high". |
| **Live engine** | In-play recalculation on goals/red cards, with the probability timeline kept | Pluggable — a paid feed drops in without touching prediction logic. |
| **Access control** | Register → pending → Super Admin approval → active, enforced at router level | The gate is real: the whole API rejects an unapproved account. |
| **Zero cost** | Free data sources, SQLite, local training, no paid API keys | Genuinely $0 to run, not "$0 with an asterisk". |
| **Test coverage** | 113 passing tests, including grounding assertions on the assistant | Tests assert *correctness of claims*, not just HTTP 200s. |

### Real weaknesses found in the audit

Ordered by how much they matter.

#### 1. Accuracy: better than first measured, still short of the market

Measured across five leagues, ~1,300 training matches each, evaluated on each
league's own held-out date-split:

| League | Model | Always-home baseline | Edge |
|---|---|---|---|
| Italian Serie A | **52.9%** | 40.1% | **+12.8** |
| German Bundesliga | 51.1% | 44.2% | +6.9 |
| Spanish La Liga | 50.7% | 49.3% | +1.4 |
| French Ligue 1 | 50.0% | 42.2% | +7.8 |
| English Premier League | 45.9% | 38.6% | +7.2 |
| **Mean** | **50.1%** | — | **+7.2** |

An earlier revision of this document called the model "the headline problem"
on the strength of the Premier League number alone. That was wrong, and wrong
in an instructive way: **the EPL is the hardest of the five to predict** — the
most competitive, with the lowest home-win rate — so judging the system by it
understated the model by about four points.

The defensible summary: a **mean of 50.1%, beating always-pick-home by a
consistent +7.2 points in every league tested**, with Serie A at 52.9%
essentially matching the 53-55% bookmaker range. Below the market overall, but
a real and reproducible edge rather than noise.

Two caveats worth keeping in view. La Liga's +1.4 edge is thin — its baseline
happened to be unusually high (49.3% home wins in that window), so the model
barely clears it there. And ~1,300 training matches per league is small; the
per-league variance above is partly genuine difficulty and partly sample size.

Where the remaining gains are, in order:

- **Cross-league training (item 6 below) is now the biggest single lever.**
  Each league currently trains its own model on its own ~1,300 matches, so
  importing five leagues produced five data-starved models rather than one
  model with 9,033 matches behind it. Pooling them with league indicators —
  or a global model with per-league fallback — is where the next real jump is.
- **Ensemble weights are hand-set** (0.30/0.35/0.35) rather than fitted.
- **A fixed weighted mean is weaker than stacking** a meta-model over the
  three model outputs.

#### 1b. Calibration collapse on small validation sets — diagnosed and fixed

Found during this audit, and worth recording because the symptom looked like a modelling
problem and wasn't.

**Symptom:** five of ten generated fixtures returned the *identical* 1X2 split
(37%/22%/41%), despite the three underlying models disagreeing per fixture — e.g.
Brentford-Chelsea had raw Elo/Poisson/GBM home-win of 0.42/0.34/0.39, while
Tottenham-Villa had 0.31/0.45/0.21. Different inputs, identical output.

**Cause:** isotonic regression is a *step function*. Fitted on only ~285 validation
matches, it produced just 12 distinct output levels for home win and **5 for draw**.
Probing the fitted calibrator directly:

```
raw home-win  0.30  0.35  0.40  0.45   ->  all map to 0.42
raw draw      0.35 ... 0.75            ->  all map to 0.50
```

Wide plateaus swallowed the ranking information the ensemble had produced. The draw
calibrator was worse than merely coarse: it mapped most of its input range to 0.50, when
draws actually occur 29.7% of the time in that window.

**Fix applied:** the calibrator now requires a minimum sample count per label before
using isotonic regression, and falls back to **Platt scaling** (sigmoid) below that
threshold. Platt scaling fits two parameters instead of an arbitrary step function, so it
stays smooth and strictly monotonic — it corrects bias without discarding the ordering
between fixtures. Isotonic remains available and is the better choice once there's enough
validation data to support it.

**Measured effect.** Regenerating the same 10 upcoming fixtures:

| | Before | After |
|---|---|---|
| Distinct 1X2 outputs across 10 fixtures | 6 | **10** |
| Fixtures sharing an identical split | 5 | **0** |
| Home-win spread | — | 0.27–0.72, σ = 0.151 |

On the backtest, accuracy moved 43.1% → 45.9% and log loss 1.079 → 1.071, with
calibration error slightly worse (0.069 → 0.082). Two honest caveats: the train/test
boundary shifted between the two runs as more fixtures arrived, so those figures are
**not a clean A/B comparison**; and the small ECE regression is the expected trade-off —
isotonic buys calibration by sacrificing discrimination, Platt does the reverse. The
unambiguous, directly verifiable win is the elimination of the collapse. A clean
before/after on a frozen split is worth running as a follow-up.

**Lesson worth generalising:** a calibration step can silently make a model *worse* while
improving its aggregate calibration metric. Plateaus look fine to a reliability diagram
and destroy per-fixture discrimination. Any future calibration change should be checked
against both a calibration metric *and* a ranking metric (AUC or simple output variance).

#### 1c. Deployed models train on 70% of the data — deliberately, for now

Measured on the five-league database. The backtest splits 70/15/15 by date,
and the model that actually serves predictions is the one trained on that
first 70% — so training stops well short of the present:

| League | Training data ends | Stale by | Matches never used |
|---|---|---|---|
| English Premier League | 2025-01-18 | ~20 months | 579 |
| Spanish La Liga | 2025-02-01 | ~20 months | 580 |
| Italian Serie A | 2025-01-19 | ~20 months | 576 |
| French Ligue 1 | 2024-12-13 | ~21 months | 512 |
| German Bundesliga | 2025-01-25 | ~20 months | 465 |

About 2,700 matches — 30%, and the most recent ones — never reach the
deployed model. Elo and Poisson are unaffected, since both are recomputed
from all matches at prediction time; it is the gradient-boosting model and
the calibrators that are frozen on older data.

**Status: deliberately left as-is** (decided September 2026). Recorded here
because the finding is real and the reasoning for the fix is non-obvious,
not because it is outstanding work.

**If it is revisited, the naive fix is wrong.** Refitting the model on 100%
of the data is safe in itself — features are computed as-of each match's
kickoff, so training on all *past* matches to predict a future one is not
leakage, and two tests pin that (`test_future_matches_are_not_read`,
`test_the_match_itself_is_not_read`).

The trap is the calibrators. They are currently fitted on the validation
slice, which the model has never seen — that is what makes the calibration
honest. Train the model on 100% and that slice becomes training data, where
the model is over-confident; calibrating against those predictions would
teach it that it is better than it is, and the calibration step would start
making probabilities worse while appearing to work. That is the same failure
shape as the isotonic collapse in 1b.

Doing it properly means **out-of-fold calibration**: train K models on K-1
folds, collect predictions on each held-out fold, and fit the calibrators on
those, so every calibration point comes from a model that never saw it. The
reported accuracy must still come from the held-out split either way —
measuring on data the model trained on would inflate it.

#### 1d. Cross-league pooling: measured, and rejected as a net loss on its own

Item 6 below called pooling all leagues into one model "the single biggest
lever left on accuracy," on the reasoning that five leagues training five
~1,300-match models separately wastes the other four leagues' data. Measured
directly rather than assumed:

| | Mean accuracy (5 original leagues) | La Liga | EPL |
|---|---|---|---|
| Per-league (this corpus, hand-set weights) | 50.1% | 50.7% | 45.9% |
| Pooled across leagues, hand-set weights | 49.7% | 48.5% (-2.2) | 42.1% (-3.8) |

Pooling alone made two leagues measurably worse and one meaningfully better
(Bundesliga +3.2), a wash-to-negative net. The likely cause: a single
GradientBoostingClassifier with one set of tree splits has to represent five
leagues' different scoring environments and home-advantage sizes through
only a 9-column one-hot flag, which a shallow (max_depth=3) tree can't
condition on cleanly -- pooling raw row count without also letting the
model specialize per league bought little.

**What actually moved accuracy, measured on the same corpus:** home/away-
specific form features (previously computed by TeamForm and never wired
into the model), fitting the ensemble blend weights per league instead of
the hand-set 0.30/0.35/0.35, and importing five more seasons per league
(2016-17 onward). Per-league models with those three changes:

| League | Accuracy | vs. always-home | Fitted weights (elo/poisson/ml) |
|---|---|---|---|
| UEFA Champions League | 59.6% | +8.7 | 0.75 / 0.10 / 0.15 |
| Portuguese Primeira Liga | 54.8% | +13.6 | 0.00 / 0.80 / 0.20 |
| Italian Serie A | 54.6% | +14.5 | 0.20 / 0.45 / 0.35 |
| Spanish La Liga | 52.9% | +5.4 | 0.20 / 0.60 / 0.20 |
| French Ligue 1 | 52.3% | +6.8 | 0.95 / 0.05 / 0.00 |
| Dutch Eredivisie | 51.9% | +9.9 | 0.35 / 0.40 / 0.25 |
| German Bundesliga | 51.1% | +11.5 | 0.55 / 0.20 / 0.25 |
| English Premier League | 49.3% | +7.5 | 0.80 / 0.00 / 0.20 |
| English Championship | 42.0% | +0.3 | 0.40 / 0.55 / 0.05 |
| **Mean (9 leagues)** | **52.1%** | **+9.8** | |

Mean across the original 5 leagues alone: 52.0%, versus 50.1% before this
round and 49.7% pooled. The fitted weights are also worth reading on their
own: they vary widely by league (Ligue 1 leans almost entirely on Elo;
Primeira Liga ignores it entirely in favor of Poisson) -- a single global
0.30/0.35/0.35 was never going to be right for all of them, which is a
second, independent reason a single pooled model underperforms per-league
ones here.

**Status: pooling code kept, not deployed.** `scripts/backtest.py
--pool-leagues` and `build_pooled_training_dataset()` still exist and work
-- pooling remains the right call for a league too new or small to train
its own model (a true fallback, which is what item 6 actually needs, not a
wholesale replacement) -- but the model_artifacts/ml_model_global.joblib
this measurement produced was deleted rather than left as the file
model_store.load_ml_model() prefers, so per-league models stay the ones
actually serving predictions. Revisiting pooling productively would mean
per-league leaf weights or a stacked meta-model over per-league models,
not a single shared tree ensemble distinguishing leagues by one-hot alone.

#### 2. Security gaps

| Issue | Risk | Status |
|---|---|---|
| `SECRET_KEY` default is published in this repo | Anyone who read the repo can forge a superadmin token | ⚠️ Now warns loudly at startup; **still needs setting before any deployment** |
| CORS was hardcoded `*` | Any origin can script the API | ✅ Fixed — now config-driven |
| No brute-force protection | Every weak password was a matter of time | ✅ Fixed — rate limiting on login/register/chat |
| No audit trail | Couldn't answer "who approved this account?" after the next status change | ✅ Fixed — append-only audit log |
| Rate limiting is in-process | Per-worker, resets on restart; multi-worker deployments get N× the limit | ⚠️ Documented; Redis upgrade path noted in code |
| No password reset | Locked-out users need DB surgery | ❌ Open (see Tier 1) |
| No email verification | Anyone can register any address | ❌ Open (see Tier 1) |
| Tokens can't be revoked | A leaked token is valid for 7 days | ❌ Open (see Tier 1) |

#### 3. Structural gaps

- **No result tracking.** Predictions are stored but never scored against what actually
  happened. The system cannot answer "how did last week's calls do?" This is the highest-value
  missing feature — see Tier 1.
- **Predictions never auto-refresh.** Generated once, then stale until someone re-runs a
  script. No scheduler.
- **One league.** All the plumbing is league-agnostic; only the data import is narrow.
- **No value/edge detection.** Deliberate (no odds), but it means the system tells you
  what's *likely*, never what's *mispriced* — and the most-likely outcome is usually a
  low-information one (over 0.5 goals at 95%).
- **No notifications.** No way to be told a high-confidence call appeared.
- **Polling, not push.** Live updates need a WebSocket to feel live.
- **`thesportsdb.py` is dead code** — documented as non-functional. Either fix or delete.

---

## Part 2 — Roadmap

Every item marked **$0** is achievable with free tooling. Items with a cost are marked
explicitly, and none are required.

### Tier 0 — Fix the model (do this before anything else)

Nothing else matters if the predictions aren't good. All **$0**.

1. **Diagnose the constant-prediction symptom.** Assert in a test that two dissimilar
   fixtures produce materially different 1X2 outputs. Log per-model outputs across a
   sample and check variance. This is likely a bug with a large payoff.
2. **Fit the ensemble weights** on the validation split by minimising log loss, instead
   of hand-setting them. Store the fitted weights with the model version.
3. **Replace the weighted mean with a stacked meta-model** — logistic regression over the
   three models' outputs. Usually a solid gain over fixed weights.
4. **Add a baseline row to every backtest**: always-home, always-draw, and the
   league's base rates. A model that can't beat always-home must not ship. Put it in the
   metrics table so it's visible, not buried in a console log.
5. **Expand the training corpus.** Import 8+ leagues × 10 seasons (~30,000 matches, all
   free from football-data.co.uk). More data is the cheapest accuracy gain available.
6. **Train per-league models** with a global fallback, rather than one model per league
   trained on that league alone.
7. **Add features that are free and known to matter**: rolling xG proxies (shots and
   shots-on-target are in the football-data.co.uk CSVs), home/away-specific form splits,
   league position gap, days since last match per competition, promoted-team flags.
8. **Time-decay weighting** on training samples — a match from 2019 should not count as
   much as one from last month.
9. **Walk-forward cross-validation** instead of a single split, so the accuracy figure
   has an error bar rather than being one lucky or unlucky slice.

### Tier 1 — Make it trustworthy and complete

10. **Result tracking and a public track record** — **$0, highest product value.**
    Score every stored prediction against the actual result once played. Surface a
    rolling hit rate by market and confidence band, plus a reliability diagram. This is
    the difference between "trust us" and a verifiable record. It also feeds back into
    detecting model drift.
11. **Prediction scheduler** — regenerate predictions on a cadence and re-fetch results.
    APScheduler in-process, or a cron job. **$0.**
12. **Password reset + email verification.** SMTP via a free tier (Brevo 300/day,
    Resend 3,000/month) or console-output tokens for a self-hosted setup. **$0.**
13. **Token revocation** — a `token_version` on the user, bumped on logout/password
    change and checked at verification. Small change, closes the leaked-token window. **$0.**
14. **Multi-league expansion** across the top European leagues plus second tiers. **$0.**
15. **WebSocket push** for live probability updates, replacing polling. FastAPI has
    native support. **$0.**
16. **Notification engine** — in-app first, then email/web push, for high-confidence
    calls and big in-play swings. Web push is free; email on a free SMTP tier. **$0.**
17. **Model-drift monitoring** — track rolling calibration error in production and alert
    when it degrades past a threshold. The metrics table already exists. **$0.**
18. **Delete or fix `thesportsdb.py`.** Dead code that documents its own brokenness is
    worse than no code.

### Tier 2 — Depth that separates it from competitors

19. **Accumulator / parlay builder** with correct correlation handling. The outcome
    registry already records mutually-exclusive groups — this is the payoff for that
    design. Naive multiplication of correlated legs is what everyone else does wrong. **$0.**
20. **Odds comparison and value detection.** The honest gap in the current design: the
    system can't tell you what's mispriced. Free odds are scrapeable from
    football-data.co.uk's historical CSVs (closing odds included) for backtesting
    value strategies, even without a live feed. Adds "expected value" alongside
    probability. **$0 for historical; live odds APIs cost money.**
21. **Player-level and lineup intelligence.** The recalculation hooks are already built.
    Free sources are weak, but FBref/Understat scraping (respecting robots.txt and rate
    limits) gets minutes, xG and availability. **$0, moderate effort, fragile.**
22. **In-play xG model** to replace the current time-scaled Poisson projection. Needs a
    live stats feed — the one genuinely paid item on this list.
23. **Referee, weather and travel sub-models.** Weather is free (Open-Meteo, no key).
    Referee card/penalty tendencies are derivable from historical data already imported.
    Travel distance from free coordinates. **$0.**
24. **Competition-specific modelling** — cup ties, two-legged knockouts, derbies, and
    end-of-season dead rubbers behave differently from league fixtures. **$0.**
25. **Simulation-based season projections** — Monte Carlo the remaining fixtures for
    title/relegation/top-four probabilities. High perceived value, pure compute. **$0.**
26. **Multi-sport expansion.** Basketball and tennis have good free data and are
    *easier* to predict than football (more scoring events = less variance). The
    architecture is football-shaped in places; this is a real refactor, not a config
    change. **$0 data, significant effort.**

### Tier 3 — Product and platform polish

27. **Upgrade the AI assistant.** It's grounded and useful today, but rule-based. Next
    steps, in order: (a) log `unknown`-intent questions and teach the parser the
    phrasings people actually use — the data is already being stored; (b) add
    multi-turn memory so "what about the other team?" resolves; (c) optionally enable the
    LLM rewriter against a **locally-run** model (Ollama) for fluency at still-zero cost.
    Keep the grounded pipeline as the source of truth regardless. **$0.**
28. **PWA + offline support** — installable, cached shell, works on a bad connection.
    Meaningful in markets where this matters most. **$0.**
29. **Export and API access for members** — CSV/JSON export, personal API keys. **$0.**
30. **Onboarding tour** and a first-run empty state that teaches the confidence scores.
31. **Automated payment verification.** The `approve` endpoint already has the Hubtel
    integration point marked. Wiring it turns manual approval into verified-then-approve.
    Cost depends on the provider's fees.
32. **i18n** — the codebase has no string extraction today.
33. **Accessibility audit** — the design tokens were validated for contrast and
    colour-vision deficiency, but no keyboard/screen-reader pass has been done.

### Tier 4 — Operational maturity

34. **CI pipeline** — run the 113 tests and the frontend build on every push. GitHub
    Actions is free for public repos. **$0.**
35. **Proper migrations** (Alembic). `ensure_schema` is a hand-rolled patcher that won't
    scale past a few columns. **$0.**
36. **Structured logging + error tracking** — GlitchTip self-hosted, or Sentry's free
    tier. **$0.**
37. **Backups** — SQLite file snapshots to a free object store. **$0.**
38. **Postgres** when concurrent writes start mattering. Free tiers exist (Neon,
    Supabase). **$0 to start.**
39. **Frontend test suite.** There is none today — Vitest + Testing Library, plus the
    Playwright flows used to verify this work manually. **$0.**
40. **Redis-backed rate limiting** once running more than one worker. Free tier
    available (Upstash). **$0 to start.**

---

## Deployment at zero cost

| Component | Free option |
|---|---|
| Frontend | Cloudflare Pages / Netlify / Vercel / GitHub Pages — all free for static builds |
| Backend | Fly.io free allowance, Render free tier (sleeps), Oracle Cloud Always Free (best: real always-on VM) |
| Database | SQLite on a persistent volume; Neon/Supabase free Postgres if needed |
| Model training | Local machine, or GitHub Actions (2,000 free minutes/month) |
| Scheduler | GitHub Actions cron — free, no server needed |
| Email | Brevo (300/day) or Resend (3,000/month) |
| Monitoring | UptimeRobot free tier; GlitchTip self-hosted |
| Error tracking | Sentry free tier (5k events/month) |

Oracle Cloud's Always Free tier (4 ARM cores, 24GB RAM) is the standout: enough to run
the API, the database, model training, *and* a local LLM for the optional assistant
rewriter, permanently, at no cost.

---

## If you only do five things

_Updated after actually measuring item 1 below (see 1d) — cross-league pooling turned
out to be a wash-to-negative on its own, so this list no longer leads with it._

1. **Done, and worth knowing what actually moved the number:** wiring in the home/away
   form split, fitting the ensemble blend weights per league instead of hand-setting
   them, and importing five more seasons per league moved the 5-league mean from 50.1%
   to 52.0% — see 1d for the full per-league table. Cross-league pooling (the version of
   this item that used to lead this list) was tried and measured worse alone; a stacked
   meta-model or per-league leaf weights would be the way to revisit it, not a single
   shared tree ensemble.
2. **Add baseline comparisons to the backtest** (item 4) — done, every backtest run now
   prints and stores always-home/draw/away, majority-class, and training-rate baselines.
3. **Build result tracking** (item 10) — done, `/api/public/track-record` grades each
   finished match's first stored prediction against the real result.
4. **Set `SECRET_KEY`, and expand to 8+ leagues** (item 5, plus the security warning
   above). Nine leagues are now imported; per-league models mean this adds *coverage*
   (more fixtures to show) more than it adds accuracy, per 1d.
5. **Schedule prediction regeneration** (item 11). Stale predictions are worse than none.

## What "best in the world" would actually require

Being honest about the ceiling: the items above would make this an unusually rigorous,
transparent, well-engineered prediction platform — better documented and more honest
about uncertainty than most commercial products.

It would still not beat the market. Professional syndicates employ PhDs, buy tracking
data at six figures a year, and model at player-event granularity. Free data sets a hard
ceiling on accuracy that no amount of engineering removes.

The defensible claim is a different one, and it's worth more than an inflated accuracy
number: **the most transparent, best-calibrated, most honestly-presented football
prediction system available — one that tells you exactly how confident it is and exactly
why, and never pretends a probability is a certainty.** That is achievable with
everything on this list, at zero cost.
