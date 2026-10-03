import asyncio
import contextlib
import logging
import threading
import time

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import get_current_user, require_active_access
from app.api.routes_access import router as access_router
from app.api.routes_admin import router as admin_router
from app.api.routes_auth import router as auth_router
from app.api.routes_betcodes import router as betcodes_router
from app.api.routes_chat import router as chat_router
from app.api.routes_matches import router as matches_router
from app.api.routes_predictions import router as predictions_router
from app.api.routes_public import router as public_router
from app.api.routes_settings import router as settings_router
from app.api.routes_teams import router as teams_router
from app.config import get_settings
from app.data.api_football_ingest import run_live_sync_from_settings
from app.data.providers.api_football import ApiFootballError, QuotaExceeded
from app.data.squad_ingest import run_lineup_check_from_settings
from app.db.migrate import init_db
from app.db.models import Match
from app.db.session import SessionLocal, engine
from app.prediction_service import build_prediction_for_match

logger = logging.getLogger(__name__)

# GitHub Actions' every-5-minute cron for sync-live-matches.yml is
# best-effort in name only: in production this project saw it actually fire
# every 1-5 *hours*, not every 5 minutes -- high-frequency schedule
# triggers are heavily throttled on GitHub's infrastructure, which is a
# platform limit, not something the workflow's own retry logic can fix. A
# web process that is already running continuously can just do this
# itself, so live scores no longer depend on GitHub's scheduler at all;
# the workflow stays only as a manual (workflow_dispatch) fallback.
LIVE_SYNC_INTERVAL_SECONDS = 300.0


def _run_live_sync_once() -> None:
    db = SessionLocal()
    try:
        report = run_live_sync_from_settings(db)
    except (ApiFootballError, QuotaExceeded) as exc:
        logger.warning("Live match sync skipped: %s", exc)
        return
    finally:
        db.close()

    if report is not None and (report.updated or report.finished):
        logger.info(
            "Live sync: %d fixture(s) seen, %d updated, %d finished",
            report.considered, report.updated, report.finished,
        )


async def _live_sync_loop() -> None:
    while True:
        try:
            # Synchronous DB + HTTP work, off the event loop so it never
            # blocks requests being served concurrently.
            await asyncio.to_thread(_run_live_sync_once)
        except Exception:  # noqa: BLE001 -- one bad poll must not kill the loop
            logger.exception("Live match sync loop hit an unexpected error")
        await asyncio.sleep(LIVE_SYNC_INTERVAL_SECONDS)


# Official lineups land roughly 60-75 minutes before kickoff, never earlier
# and never revised after -- the daily prediction batch runs hours or days
# before that, so it can only ever know about injuries/suspensions reported
# ahead of time (see app.features.squad_strength), not a last-minute
# tactical rest. Same 5-minute cadence as the live-score poll above: a
# lineup can't change faster than that, so there's nothing to gain from
# checking more often, and this shares that loop's own request budget.
LINEUP_CHECK_INTERVAL_SECONDS = 300.0
LINEUP_CHECK_WINDOW_MINUTES = 90


def _run_lineup_check_once() -> None:
    db = SessionLocal()
    try:
        run = run_lineup_check_from_settings(db, window_minutes=LINEUP_CHECK_WINDOW_MINUTES)
        if run is None:
            return
        for match_id in run.newly_confirmed:
            match = db.get(Match, match_id)
            if match is None:
                continue
            try:
                # A new Prediction snapshot, not an edit to the existing one
                # -- the same history every other re-generation already
                # produces (see /api/matches/{id}/prediction-history), so a
                # pre-lineup prediction a user already saw stays on the
                # record rather than silently changing under them.
                build_prediction_for_match(db, match)
            except Exception:  # noqa: BLE001 -- one match's failure must not strand the rest, nor get silently retried forever
                # import_lineup already marked this match's lineup as
                # fetched, so a bare `continue` here would mean this match
                # never gets another chance at regeneration once the next
                # poll sees already_had_lineup and skips re-fetching --
                # logged loudly rather than silently dropped for exactly
                # that reason.
                logger.exception("Could not regenerate prediction for match %d after lineup confirmation", match_id)
    except (ApiFootballError, QuotaExceeded) as exc:
        logger.warning("Lineup check skipped: %s", exc)
        return
    finally:
        db.close()

    if run.newly_confirmed:
        logger.info(
            "Lineup check: %d match(es) considered, %d prediction(s) regenerated on confirmed lineup",
            run.considered, len(run.newly_confirmed),
        )


async def _lineup_check_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(_run_lineup_check_once)
        except Exception:  # noqa: BLE001 -- one bad poll must not kill the loop
            logger.exception("Lineup check loop hit an unexpected error")
        await asyncio.sleep(LINEUP_CHECK_INTERVAL_SECONDS)

# Schema state, reported by /api/health.
_DB_LOCK = threading.Lock()
_DB_STATE: dict = {"ready": False, "error": None, "last_attempt": 0.0}
_RETRY_INTERVAL_SECONDS = 30.0


def _try_init_database(attempts: int = 3, base_delay: float = 2.0) -> bool:
    """Create and migrate the schema, tolerating a database that is asleep.

    This used to be a bare ``init_db(engine)`` at import time, which meant any
    database hiccup killed the process before it served a single request. A
    free-tier Postgres that scales to zero produces exactly those hiccups, and
    a host then reports "Failed service" with the actual reason buried in a
    log you have to go find.

    A web process that cannot reach its database should start anyway, say so
    on ``/api/health``, and recover on its own when the database comes back.
    That is diagnosable from outside. A container that exits is not.
    """

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            init_db(engine)
        except Exception as exc:  # noqa: BLE001 -- any driver error must not kill the process
            last_error = exc
            if attempt < attempts:
                time.sleep(base_delay * attempt)
            continue

        with _DB_LOCK:
            _DB_STATE.update(ready=True, error=None, last_attempt=time.monotonic())
        logger.info("Database schema ready.")
        return True

    with _DB_LOCK:
        _DB_STATE.update(
            ready=False,
            error=f"{type(last_error).__name__}: {last_error}",
            last_attempt=time.monotonic(),
        )
    logger.error(
        "Database unreachable after %d attempts -- the API is running but every data "
        "endpoint will fail until it recovers. Last error: %s",
        attempts,
        last_error,
    )
    return False


_try_init_database()

settings = get_settings()

if settings.secret_key_is_default:
    # Session tokens are HMAC-signed with this value. Anyone who knows it can
    # mint a token for any user id, including a superadmin -- and the default
    # is published in this repository. Loud on purpose.
    logger.warning(
        "SECRET_KEY is the published development default -- session tokens can be forged by anyone "
        "who has read this repository. Set SECRET_KEY in .env before exposing this API to a network."
    )

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(_live_sync_loop()), asyncio.create_task(_lineup_check_loop())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="AI Football Prediction & Analytics System",
    description=(
        "Ensemble (Elo + Poisson + Gradient Boosting) football prediction API with a "
        "Global Most-Likely Outcome engine. All predictions include confidence, "
        "data-quality and model-agreement scores -- probabilities are never presented "
        "as guarantees."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Headers a browser honors on every response, API or not.

    No Content-Security-Policy here -- CSP is about what a *page* is allowed
    to load, and the one HTML this service ever serves to a browser directly
    is /docs (Swagger UI), which loads its own JS/CSS from a CDN; a strict
    default-src would break it for no real benefit, since the actual
    frontend is a separate static site that sets its own CSP (see
    frontend/public/_headers). X-Frame-Options and nosniff carry no such
    tradeoff -- they're free, and they still protect /docs and this API's
    own JSON error pages from being framed or MIME-sniffed into something
    they're not.
    """

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Render terminates TLS in front of this process, so every request a
    # browser actually makes is HTTPS even though uvicorn itself sees HTTP --
    # safe to tell the browser to enforce that for a year regardless.
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# /api/auth/* (register/login/status) and /api/public/* (landing-page figures)
# are intentionally open. /api/access/* needs only a logged-in account (an
# expired user must still be able to redeem a new code). Teams, matches and
# chat -- full match detail, live tracking, team history -- require a live
# access grant. predictions_router is mounted with only the login
# requirement: most of its routes add `require_active_access` themselves
# (see routes_predictions.py), but /free-picks deliberately doesn't, so a
# logged-in account with no code yet still gets a genuine, if small, taste
# of the model instead of a wall of 403s.
app.include_router(auth_router)
app.include_router(public_router)
app.include_router(admin_router)
app.include_router(settings_router)
app.include_router(access_router)
app.include_router(chat_router, dependencies=[Depends(require_active_access)])
app.include_router(teams_router, dependencies=[Depends(require_active_access)])
app.include_router(matches_router, dependencies=[Depends(require_active_access)])
app.include_router(betcodes_router)
app.include_router(predictions_router, dependencies=[Depends(get_current_user)])


@app.exception_handler(SQLAlchemyError)
def database_unavailable(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Turn a database outage into an honest 503 instead of a bare 500.

    Two reasons, and the second is the one that costs hours.

    An unhandled exception is caught above the CORS middleware, so the 500 it
    produces carries no ``Access-Control-Allow-Origin`` header. The browser
    then reports a *CORS* failure -- and you go and check your origins, and
    your origins are fine, because the database was the problem all along.
    Returning a response from here instead sends it back out through the CORS
    middleware, so the error the browser shows is the error that happened.

    And 503 is simply true where 500 is not: the service is fine, its
    dependency is not, and the difference tells you where to look.
    """

    logger.error("Database error serving %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={
            "detail": "The database is unavailable. This is usually a sleeping or "
                      "over-quota database rather than a fault in the API -- check "
                      "/api/health, which reports the connection state.",
        },
    )


@app.get("/")
def root() -> dict:
    """A signpost at the base URL.

    Without this, anyone opening the service's root -- which is exactly what
    you do after a deploy, to check it worked -- gets FastAPI's bare
    ``{"detail":"Not Found"}``. That reads as a broken deployment when the
    API is in fact running perfectly, just with no route mounted at ``/``.
    Pointing at the docs and the health check costs nothing and answers the
    question the visitor actually had.
    """

    return {
        "service": "AI Football Prediction & Analytics System",
        "status": "ok",
        "version": app.version,
        "docs": "/docs",
        "health": "/api/health",
        "public_data": ["/api/public/stats", "/api/public/accuracy", "/api/public/fixtures"],
        "note": (
            "Predictions, teams and matches require a logged-in account with a live access "
            "code redemption. This is the API only -- the web app is deployed separately."
        ),
    }


@app.get("/api/health")
def health() -> dict:
    """Liveness, plus whether the database is actually reachable.

    ``status`` answers "is this process up", which is what a host's health
    check needs -- reporting unhealthy because the database is asleep would
    have the host kill a process that is about to recover. ``database``
    answers the separate question of whether it can serve data, which is the
    one you want when the site looks empty.

    The error text stays in the logs. A connection error can carry the
    database host, and this endpoint is public.
    """

    with _DB_LOCK:
        ready = _DB_STATE["ready"]
        stale = time.monotonic() - _DB_STATE["last_attempt"] > _RETRY_INTERVAL_SECONDS

    if not ready and stale:
        # Self-heal: once the database wakes, the schema gets created without
        # anyone having to redeploy.
        ready = _try_init_database(attempts=1)

    return {"status": "ok", "database": "ready" if ready else "unavailable"}
