"""Prod settings validation.

Startup is the last moment a committed dev secret is cheap to catch — after it,
/webhooks/payments will accept events signed with a string that lives in the
public source tree, and that route can enable payout accounts and move money.
"""

import pytest
from pydantic import ValidationError

from app.core.config import _DEV_WEBHOOK_SECRET, Settings

_PROD_KWARGS = dict(
    environment="prod",
    jwt_secret="a-strong-secret-of-at-least-32-bytes!",
    public_base_url="https://api.example.com",
)


def test_prod_rejects_the_committed_webhook_secret():
    with pytest.raises(ValidationError, match="PAYMENTS_WEBHOOK_SECRET"):
        Settings(**_PROD_KWARGS, payments_webhook_secret=_DEV_WEBHOOK_SECRET)


def test_prod_rejects_a_short_webhook_secret():
    with pytest.raises(ValidationError, match="PAYMENTS_WEBHOOK_SECRET"):
        Settings(**_PROD_KWARGS, payments_webhook_secret="too-short")


def test_prod_accepts_a_strong_webhook_secret():
    settings = Settings(**_PROD_KWARGS, payments_webhook_secret="f" * 64)
    assert settings.environment == "prod"


def test_dev_keeps_the_committed_webhook_default():
    settings = Settings(environment="dev", payments_webhook_secret=_DEV_WEBHOOK_SECRET)
    assert settings.payments_webhook_secret == _DEV_WEBHOOK_SECRET


def test_prod_still_rejects_the_dev_jwt_secret():
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(
            environment="prod",
            jwt_secret="dev-only-secret-change-me",
            public_base_url="https://api.example.com",
            payments_webhook_secret="f" * 64,
        )
