from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-only-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TOOLBELT_", extra="ignore")

    app_name: str = "ToolBelt API"
    environment: str = "dev"  # dev | test | prod
    database_url: str = "sqlite:///./toolbelt.db"
    jwt_secret: str = _DEV_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    # Matching defaults
    default_search_radius_km: float = 25.0
    max_search_radius_km: float = 100.0
    # Storage
    upload_dir: str = "./uploads"
    # Public origin, used to build URLs a third party redirects back to
    # (currently Stripe Connect onboarding return/refresh).
    public_base_url: str = "http://localhost:8000"
    # Payments
    # Country and currency used when provisioning a worker's Stripe Connect
    # account. Single-country for now; becomes per-worker at second-market.
    connect_country: str = "us"
    default_currency: str = "usd"
    platform_fee_bps: int = 1500  # 15% take-rate on completed jobs
    payments_webhook_secret: str = "dev-webhook-secret-0123456789abcdef"
    stripe_secret_key: str | None = None

    @model_validator(mode="after")
    def _prod_requires_strong_secret(self) -> "Settings":
        if self.environment == "prod" and (
            self.jwt_secret == _DEV_SECRET or len(self.jwt_secret.encode()) < 32
        ):
            raise ValueError(
                "TOOLBELT_JWT_SECRET must be set to a random value of at least "
                "32 bytes in prod (e.g. `openssl rand -hex 32`)"
            )
        return self

    @model_validator(mode="after")
    def _prod_requires_public_url(self) -> "Settings":
        # Stripe rejects localhost onboarding URLs, and a worker redirected to
        # localhost after onboarding lands nowhere. Fail at startup, not at the
        # first payout attempt.
        if self.environment == "prod" and (
            "localhost" in self.public_base_url or "127.0.0.1" in self.public_base_url
        ):
            raise ValueError(
                "TOOLBELT_PUBLIC_BASE_URL must be a real public https origin in prod "
                "(Stripe Connect onboarding redirects to it)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
