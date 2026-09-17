from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import get_current_user
from app.api.routes_admin import router as admin_router
from app.api.routes_auth import router as auth_router
from app.api.routes_matches import router as matches_router
from app.api.routes_predictions import router as predictions_router
from app.api.routes_teams import router as teams_router
from app.db.models import Base
from app.db.session import engine

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="AI Football Prediction & Analytics System",
    description=(
        "Ensemble (Elo + Poisson + Gradient Boosting) football prediction API with a "
        "Global Most-Likely Outcome engine. All predictions include confidence, "
        "data-quality and model-agreement scores -- probabilities are never presented "
        "as guarantees."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# /api/auth/* (register/login) is intentionally open -- everything else
# requires a logged-in, superadmin-approved account.
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(teams_router, dependencies=[Depends(get_current_user)])
app.include_router(matches_router, dependencies=[Depends(get_current_user)])
app.include_router(predictions_router, dependencies=[Depends(get_current_user)])


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
