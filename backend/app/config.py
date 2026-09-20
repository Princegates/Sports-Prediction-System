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
    access_redeem_rate_limit_attempts: int = 10
    access_redeem_rate_limit_window_seconds: int = 300
    password_change_rate_limit_attempts: int = 5
    password_change_rate_limit_window_seconds: int = 300

    # --- Optional AI assistant rewriter (see app/assistant/llm.py) --------
    # Left disabled so the assistant runs entirely locally at zero cost. The
    # grounded pipeline produces the answer either way; this only affects
    # phrasing. All five are also editable in the admin Settings panel
    # (group "assistant"), which overrides these -- a key rotation or
    # provider swap is then a setting, not a redeploy.
    #
    # The base_url/model defaults point at Google AI Studio's Gemini API,
    # the one mainstream option with a real, no-credit-card free tier that
    # also happens to speak the OpenAI-compatible /chat/completions shape
    # this module expects. Still fully inert without an API key.
    #
    # Flash-Lite over the flagship Flash model on purpose: Google's free
    # tier caps the flagship model at ~20 requests/day (a chat rewriter
    # firing on every message would burn through that in minutes), while
    # Flash-Lite gets 500/day -- and pure rephrasing (this module's whole
    # job) doesn't need the flagship's extra reasoning capability anyway.
    assistant_llm_enabled: bool = False
    assistant_llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    assistant_llm_model: str = "gemini-3.5-flash-lite"
    assistant_llm_api_key: str = ""
    assistant_llm_timeout: float = 20.0

    # Optional outbound email, for handing an access code to the person who
    # paid for it. Everything works without these -- the admin just copies the
    # code out of the UI and sends it themselves. See app/mailer.py.
    #
    # Port 465 means implicit TLS; 587 means STARTTLS. smtp_user/password are
    # only needed if the provider authenticates, which almost all do.
    # API-Football (api-sports.io). Optional -- openfootball covers the five
    # domestic leagues for free; this is spent on what it cannot do. Settable
    # here or in the admin panel, which overrides this.
    api_football_key: str = ""
    api_football_host: str = "v3.football.api-sports.io"
    # Pro allows 7,500/day and 300/minute; Free is 100 and 10. Defaults match
    # Pro, and both are settable in the admin panel -- a plan change is a
    # setting, not a redeploy.
    api_football_daily_budget: int = 7500
    api_football_per_minute: int = 300

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    smtp_timeout: float = 15.0

    # Alternative to raw SMTP: a Resend API key sends over HTTPS instead of
    # an SMTP port. Some hosts (many free-tier PaaS platforms among them)
    # block outbound SMTP entirely -- 25, 465 and 587 alike -- as a blanket
    # anti-abuse measure, which no amount of host/port tweaking gets around.
    # HTTPS to a known API is the one outbound path that's essentially never
    # blocked, since the app's own traffic depends on it. Takes priority over
    # SMTP when set; smtp_from still supplies the From address either way.
    resend_api_key: str = ""

    # Put in the email so the recipient knows where to redeem. Not used for
    # anything else.
    public_site_url: str = ""

    @property
    def normalized_database_url(self) -> str:
        """``database_url`` in a form SQLAlchemy 2 actually accepts.

        Managed Postgres providers (Neon, Supabase, Railway, Heroku) hand out
        connection strings beginning ``postgres://``. SQLAlchemy 2 removed
        that alias, so pasting the provider's URL straight into ``.env``
        fails at import with an unhelpful "Can't load plugin" error. And bare
        ``postgresql://`` resolves to psycopg2, which this project doesn't
        install -- it uses psycopg 3.

        Rewriting both to ``postgresql+psycopg://`` means the copy-paste path
        works, which matters because that's the path everyone takes.
        """

        url = self.database_url.strip()
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        return url

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
