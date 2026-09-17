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

    # Signs session tokens (app/auth/tokens.py). This default is fine for
    # local development; set your own in .env for any real deployment so
    # sessions can't be forged by anyone who has read this file.
    secret_key: str = "dev-secret-change-me-in-production"
    session_ttl_seconds: int = 7 * 24 * 60 * 60

    # Comma-separated list of browser origins allowed to call this API, or
    # "*" for any. Tokens are sent in an Authorization header rather than a
    # cookie, so "*" is not the credential-leak it would be for cookie auth
    # -- but it does let any page on the internet script this API on a
    # victim's behalf if it gets hold of a token, so a real deployment should
    # name its own origins here.
    cors_allow_origins: str = "*"

    # Brute-force protection on the credential endpoints, counted per client
    # IP in-process (see app/api/rate_limit.py for why that's the right
    # trade-off at this scale and when it stops being one).
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_seconds: int = 300
    register_rate_limit_attempts: int = 5
    register_rate_limit_window_seconds: int = 3600
    chat_rate_limit_messages: int = 40
    chat_rate_limit_window_seconds: int = 300

    # --- Optional AI assistant rewriter (see app/assistant/llm.py) --------
    # Left disabled so the assistant runs entirely locally at zero cost. The
    # grounded pipeline produces the answer either way; this only affects
    # phrasing.
    assistant_llm_enabled: bool = False
    assistant_llm_base_url: str = ""
    assistant_llm_model: str = "llama3.2"
    assistant_llm_api_key: str = ""
    assistant_llm_timeout: float = 20.0

    @property
    def cors_origins_list(self) -> list[str]:
        raw = (self.cors_allow_origins or "").strip()
        if raw in ("", "*"):
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def secret_key_is_default(self) -> bool:
        return self.secret_key == "dev-secret-change-me-in-production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
