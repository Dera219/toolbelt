"""SMS delivery.

Dev and test use an in-process sender with an inspectable outbox; production
sends through Twilio. A market that needs a local aggregator implements
`SmsSender` and is selected in `get_sms_sender` — nothing else changes.

Why this matters more than it looks: phone verification gates worker vetting
(`submit_vetting` requires `phone_verified`), vetting gates `VERIFIED`, and
`notify_job_posted` only targets verified workers. With no SMS provider a worker
cannot finish signup at all, so the marketplace has no supply side. That was the
state of the deployment until this file learned to send.

Delivery failures here are *not* best-effort, unlike push. A dropped
notification is recoverable; a dropped verification code leaves someone staring
at an empty box with no way forward, so `send` raises and the caller's
transaction rolls the challenge row back — which also means the 60-second resend
cooldown is not spent on a code that never arrived.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)

TWILIO_API_ROOT = "https://api.twilio.com/2010-04-01"

# Twilio reports these on the message resource rather than as an HTTP error: the
# request was accepted and delivery then failed.
TWILIO_FAILURE_STATUSES = frozenset({"failed", "undelivered"})


class SmsNotConfigured(RuntimeError):
    """No provider is wired. The route answers 503 rather than 500."""


class SmsDeliveryError(RuntimeError):
    """The provider refused or could not deliver. Upstream fault, not a crash."""


def mask_phone(phone: str) -> str:
    """Last four digits only.

    A verification code is useless without the number it went to, and logs are
    the least-guarded copy of any system. Never log the message body either —
    it contains the code itself.
    """
    digits = [c for c in phone if c.isdigit()]
    return f"***{''.join(digits[-4:])}" if len(digits) >= 4 else "***"


class SmsSender(Protocol):
    def send(self, phone: str, message: str) -> None: ...


class DevSmsSender:
    """Logs instead of sending. Keeps an outbox so dev flows and tests can read codes."""

    outbox: list[tuple[str, str]] = []

    def send(self, phone: str, message: str) -> None:
        self.outbox.append((phone, message))
        logger.info("SMS to %s: %s", phone, message)

    def reset(self) -> None:
        self.outbox.clear()


class TwilioSmsSender:
    """Posts to Twilio's Messages resource.

    Uses `urllib` rather than a client library to match the Expo sender in
    notifications/push.py and to keep the runtime dependency set unchanged —
    this is one form-encoded POST with basic auth.
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        *,
        from_number: str = "",
        messaging_service_sid: str = "",
        timeout: float = 10.0,
    ) -> None:
        if not account_sid or not auth_token:
            raise SmsNotConfigured("Twilio account SID and auth token are both required")
        if not from_number and not messaging_service_sid:
            raise SmsNotConfigured(
                "Set either a Twilio Messaging Service SID or a From number"
            )
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number
        self._messaging_service_sid = messaging_service_sid
        self._timeout = timeout

    @property
    def _auth_header(self) -> str:
        raw = f"{self._account_sid}:{self._auth_token}".encode()
        return f"Basic {b64encode(raw).decode()}"

    def send(self, phone: str, message: str) -> None:
        fields = {"To": phone, "Body": message}
        # A Messaging Service owns the number pool, opt-out handling and the
        # compliance registration, so it wins when both are configured.
        if self._messaging_service_sid:
            fields["MessagingServiceSid"] = self._messaging_service_sid
        else:
            fields["From"] = self._from_number

        request = urllib.request.Request(
            f"{TWILIO_API_ROOT}/Accounts/{self._account_sid}/Messages.json",
            data=urllib.parse.urlencode(fields).encode(),
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            # Twilio answers with a JSON body carrying its own numeric code —
            # 21211 is a malformed number, 21608 an unverified number on a trial
            # account. Log it; never return it, since it echoes the recipient.
            detail = exc.read().decode(errors="replace")[:500]
            logger.warning(
                "Twilio rejected a message to %s: HTTP %s %s",
                mask_phone(phone),
                exc.code,
                detail,
            )
            raise SmsDeliveryError("The SMS provider rejected the request") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            logger.warning("Twilio unreachable for %s: %s", mask_phone(phone), exc)
            raise SmsDeliveryError("The SMS provider is unreachable") from exc

        status = body.get("status")
        if status in TWILIO_FAILURE_STATUSES:
            logger.warning(
                "Twilio accepted then failed a message to %s: status=%s error_code=%s",
                mask_phone(phone),
                status,
                body.get("error_code"),
            )
            raise SmsDeliveryError("The SMS could not be delivered")

        logger.info("SMS queued to %s (sid=%s status=%s)", mask_phone(phone), body.get("sid"), status)


_dev_sender = DevSmsSender()


def sms_is_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and (settings.twilio_from_number or settings.twilio_messaging_service_sid)
    )


def check_sms_configured() -> None:
    """Called at startup so a missing provider is visible before a user hits it.

    Deliberately a loud log rather than a refusal to boot. Everything else in
    the API — jobs, offers, payments — works without SMS, and taking the whole
    deployment down over it would trade a broken signup for an outage.
    """
    if sms_is_configured() or get_settings().environment != "prod":
        return
    logger.error(
        "No SMS provider configured in prod. Phone verification will fail, which "
        "blocks worker vetting and therefore all job notifications. Set "
        "TOOLBELT_TWILIO_ACCOUNT_SID, TOOLBELT_TWILIO_AUTH_TOKEN and either "
        "TOOLBELT_TWILIO_MESSAGING_SERVICE_SID or TOOLBELT_TWILIO_FROM_NUMBER."
    )


def get_sms_sender() -> SmsSender:
    settings = get_settings()
    if sms_is_configured():
        return TwilioSmsSender(
            settings.twilio_account_sid,
            settings.twilio_auth_token,
            from_number=settings.twilio_from_number,
            messaging_service_sid=settings.twilio_messaging_service_sid,
        )
    if settings.environment == "prod":
        raise SmsNotConfigured(
            "No SMS provider is configured, so phone verification cannot be offered"
        )
    return _dev_sender
