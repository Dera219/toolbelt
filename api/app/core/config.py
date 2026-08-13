from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-only-secret-change-me"
_DEV_WEBHOOK_SECRET = "dev-webhook-secret-0123456789abcdef"


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
    # Rate limiting. Redis is required in prod: without shared counters, every
    # extra worker multiplies the effective limit.
    rate_limit_enabled: bool = True
    redis_url: str | None = None
    trust_proxy_headers: bool = False
    # Sessions
    refresh_token_expire_days: int = 30
    # Social sign-in. Comma-separated client IDs (audiences) per provider; a
    # provider with none configured is hidden from the app and refuses tokens.
    # Native and web builds have different client IDs, hence the list.
    google_client_ids: str = ""
    apple_client_ids: str = ""
    microsoft_client_ids: str = ""
    # Public origin, used to build URLs a third party redirects back to
    # (currently Stripe Connect onboarding return/refresh).
    public_base_url: str = "http://localhost:8000"
    # Payments
    # Country and currency used when provisioning a worker's Stripe Connect
    # account. Single-country for now; becomes per-worker at second-market.
    connect_country: str = "us"
    default_currency: str = "usd"
    platform_fee_bps: int = 1500  # 15% take-rate on completed jobs
    # Internal HMAC secret for POST /webhooks/payments — the fake-provider
    # event shape the test suite drives. NOT a Stripe value.
    payments_webhook_secret: str = _DEV_WEBHOOK_SECRET
    # Signing secret (whsec_…) for POST /webhooks/stripe. Unset disables the
    # route (it answers 503); payout state then relies on polling alone, which
    # is correct but slower.
    stripe_webhook_secret: str | None = None
    stripe_secret_key: str | None = None
    # Public by design — returned to the app so the native sheet can
    # initialise without a second place to configure it.
    stripe_publishable_key: str | None = None

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
    def _prod_requires_webhook_secret(self) -> "Settings":
        # /webhooks/payments can enable payout accounts and flush transfers, so
        # the committed default would let anyone who has read the source sign a
        # valid event. Refuse to start rather than run with a public secret.
        if self.environment == "prod" and (
            self.payments_webhook_secret == _DEV_WEBHOOK_SECRET
            or len(self.payments_webhook_secret.encode()) < 32
        ):
            raise ValueError(
                "TOOLBELT_PAYMENTS_WEBHOOK_SECRET must be set to a random value of "
                "at least 32 bytes in prod (e.g. `openssl rand -hex 32`)"
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
