from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application configuration loaded from environment variables / .env file.

    Required values have no default — the app refuses to start if they are missing.
    This is intentional: a missing JWT_SECRET in production must NEVER fall back
    to a hard-coded value, since that value would be public in the source code.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required — no defaults
    jwt_secret: str
    openrouter_api_key: str

    # Optional with safe defaults
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 1 day
    database_url: str = "sqlite:///./backend/data/qcv_app.db"
    cors_allowed_origins: list[str] = ["http://localhost:8000"]

    # Microsoft / Outlook OAuth (optional — only required if email inbox sync is used)
    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = None
    microsoft_tenant_id: str = "common"
    microsoft_redirect_uri: str = "http://localhost:8000/api/oauth/microsoft/callback"


settings = Settings()
