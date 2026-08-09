"""Push delivery abstraction.

Mirrors the SMS seam in identity/sms.py: dev and test use an in-process sender
with an inspectable outbox, production sends through Expo's push service.

Why Expo rather than APNs/FCM directly: the app is Expo-managed, so Expo already
holds the credentials for both platforms and exposes one endpoint. If the app
ever ejects, this is the only file that changes.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


@dataclass
class PushMessage:
    to: str
    title: str
    body: str
    data: dict = field(default_factory=dict)


class PushSender(Protocol):
    def send(self, messages: list[PushMessage]) -> None: ...


class DevPushSender:
    """Logs instead of sending. Keeps an outbox so dev flows and tests can read it."""

    outbox: list[PushMessage] = []

    def send(self, messages: list[PushMessage]) -> None:
        for m in messages:
            self.outbox.append(m)
            logger.info("PUSH to %s: %s — %s", m.to, m.title, m.body)

    def reset(self) -> None:
        self.outbox.clear()


class ExpoPushSender:
    """Posts to Expo's push service.

    Delivery is best-effort by design. A push that fails must never roll back
    the job or payment transaction that triggered it — a worker missing a
    notification is recoverable, a lost booking is not.
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def send(self, messages: list[PushMessage]) -> None:
        if not messages:
            return
        payload = [
            {"to": m.to, "title": m.title, "body": m.body, "data": m.data, "sound": "default"}
            for m in messages
        ]
        req = urllib.request.Request(
            EXPO_PUSH_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            logger.warning("Push delivery failed for %d message(s): %s", len(messages), exc)
            return

        # Expo returns a per-message receipt; DeviceNotRegistered means the token
        # is dead and should be pruned by the caller on a later pass.
        for item in body.get("data", []) or []:
            if item.get("status") == "error":
                logger.warning(
                    "Push rejected: %s (%s)",
                    item.get("message"),
                    (item.get("details") or {}).get("error"),
                )


_dev_sender = DevPushSender()


def get_push_sender() -> PushSender:
    settings = get_settings()
    if settings.environment == "prod":
        return ExpoPushSender()
    return _dev_sender
