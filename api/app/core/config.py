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
    # Payments
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
