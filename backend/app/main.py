import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import get_current_user
from app.api.routes_admin import router as admin_router
from app.api.routes_auth import router as auth_router
from app.api.routes_chat import router as chat_router
from app.api.routes_matches import router as matches_router
from app.api.routes_predictions import router as predictions_router
from app.api.routes_public import router as public_router
from app.api.routes_teams import router as teams_router
from app.config import get_settings
from app.db.migrate import init_db
from app.db.session import engine

logger = logging.getLogger(__name__)

init_db(engine)

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
# are intentionally open. Everything else requires a logged-in, superadmin-
# approved account.
app.include_router(auth_router)
app.include_router(public_router)
app.include_router(admin_router)
app.include_router(chat_router)
app.include_router(teams_router, dependencies=[Depends(get_current_user)])
app.include_router(matches_router, dependencies=[Depends(get_current_user)])
app.include_router(predictions_router, dependencies=[Depends(get_current_user)])


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
            "Predictions, teams, matches and the AI assistant require an account approved "
            "by a superadmin. This is the API only -- the web app is deployed separately."
        ),
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
