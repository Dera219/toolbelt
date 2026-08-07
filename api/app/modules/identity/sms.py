"""SMS delivery abstraction. Dev/test use an in-process sender; production swaps in
Twilio (or a local aggregator per market) behind the same interface."""

import logging
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class SmsSender(Protocol):
    def send(self, phone: str, message: str) -> None: ...


class DevSmsSender:
    """Logs instead of sending. Keeps an outbox so dev flows and tests can read codes."""

    outbox: list[tuple[str, str]] = []

    def send(self, phone: str, message: str) -> None:
        self.outbox.append((phone, message))
        logger.info("SMS to %s: %s", phone, message)


_dev_sender = DevSmsSender()


def get_sms_sender() -> SmsSender:
    settings = get_settings()
    if settings.environment == "prod":
        raise NotImplementedError("Wire a real SMS provider (Twilio) before prod")
    return _dev_sender
