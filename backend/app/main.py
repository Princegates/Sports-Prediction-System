from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

app.include_router(teams_router)
app.include_router(matches_router)
app.include_router(predictions_router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
