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
from app.db.migrate import init_db
from app.db.session import engine

logger = logging.getLogger(__name__)

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

app = FastAPI(
    title="AI Football Prediction & Analytics System",
    description=(
        "Ensemble (Elo + Poisson + Gradient Boosting) football prediction API with a "
        "Global Most-Likely Outcome engine. All predictions include confidence, "
        "data-quality and model-agreement scores -- probabilities are never presented "
        "as guarantees."
    ),
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
