from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./sports_prediction.db"

    # TheSportsDB rotated their shared free key from "3" to "123" at some
    # point after this project was built -- a personal key is a paid-Patreon
    # perk, so this shared free key is what we use. If they rotate it again,
    # override via THESPORTSDB_API_KEY in .env, no code change needed.
    thesportsdb_api_key: str = "123"

    ensemble_weight_elo: float = 0.30
    ensemble_weight_poisson: float = 0.35
    ensemble_weight_ml: float = 0.35

    home_advantage_elo: float = 60.0
    elo_k_factor: float = 20.0
    elo_start_rating: float = 1500.0

    # Minimum number of prior matches a team needs before we trust its
    # ratings enough to include a prediction in "high confidence" output.
    min_matches_for_confidence: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
