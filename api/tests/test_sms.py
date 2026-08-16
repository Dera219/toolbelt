"""Tests for SMS delivery and provider selection.

Phone verification is the narrow neck of the whole supply side: it gates
`submit_vetting`, which gates VERIFIED, which is the only thing
`notify_job_posted` will target. A silent failure here does not look like an SMS
bug — it looks like a marketplace with no workers in it.
"""

import urllib.error
from base64 import b64encode

import pytest

from app.core.config import Settings
from app.modules.identity import sms as sms_module
from app.modules.identity.sms import (
    DevSmsSender,
    SmsDeliveryError,
    SmsNotConfigured,
    TwilioSmsSender,
    get_sms_sender,
    mask_phone,
    sms_is_configured,
)
from tests.conftest import login, register

PHONE = "+13015550123"
SID = "AC" + "0" * 32
TOKEN = "auth-token-value"


def use_settings(monkeypatch, **kwargs):
    """Point the module at a freshly built Settings, bypassing the lru_cache."""
    settings = Settings(**kwargs)
    monkeypatch.setattr(sms_module, "get_settings", lambda: settings)
    return settings


class FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def capture_urlopen(monkeypatch, payload=b'{"sid":"SM1","status":"queued"}'):
    """Record the single request Twilio would receive."""
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        seen["body"] = request.data.decode()
        seen["timeout"] = timeout
        return FakeResponse(payload)

    monkeypatch.setattr(sms_module.urllib.request, "urlopen", fake_urlopen)
    return seen


class TestConfiguration:
    def test_unconfigured_by_default(self, monkeypatch):
        use_settings(monkeypatch)
        assert sms_is_configured() is False

    def test_a_from_number_is_enough(self, monkeypatch):
        use_settings(
            monkeypatch,
            twilio_account_sid=SID,
            twilio_auth_token=TOKEN,
            twilio_from_number="+15005550006",
        )
        assert sms_is_configured() is True

    def test_a_messaging_service_is_enough(self, monkeypatch):
        use_settings(
            monkeypatch,
            twilio_account_sid=SID,
            twilio_auth_token=TOKEN,
            twilio_messaging_service_sid="MG" + "0" * 32,
        )
        assert sms_is_configured() is True

    def test_credentials_without_a_sender_are_not_enough(self, monkeypatch):
        # Twilio needs somewhere to send *from*; credentials alone would fail at
        # the first real request instead of at startup.
        use_settings(monkeypatch, twilio_account_sid=SID, twilio_auth_token=TOKEN)
        assert sms_is_configured() is False


class TestApiKeyCredentials:
    """An API key is the preferred credential: revocable on its own, and
    restrictable to creating Messages so a leak cannot read history or spend the
    balance. The account auth token can do all of that and rotating it breaks
    every other integration at once."""

    KEY_SID = "SK" + "1" * 32
    KEY_SECRET = "key-secret-value"

    def test_an_api_key_pair_counts_as_configured(self, monkeypatch):
        use_settings(
            monkeypatch,
            twilio_account_sid=SID,
            twilio_api_key_sid=self.KEY_SID,
            twilio_api_key_secret=self.KEY_SECRET,
            twilio_from_number="+15005550006",
        )
        # Note: no auth token at all.
        assert sms_is_configured() is True

    def test_authenticates_as_the_key_but_addresses_the_account(self, monkeypatch):
        seen = capture_urlopen(monkeypatch)
        TwilioSmsSender(
            SID,
            "",
            api_key_sid=self.KEY_SID,
            api_key_secret=self.KEY_SECRET,
            from_number="+15005550006",
        ).send(PHONE, "code 123456")

        # The SID stays in the path while the key authenticates. Swapping them
        # yields a 404 on a URL that looks entirely plausible.
        assert seen["url"] == f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json"
        expected = b64encode(f"{self.KEY_SID}:{self.KEY_SECRET}".encode()).decode()
        assert seen["headers"]["authorization"] == f"Basic {expected}"

    def test_an_api_key_wins_over_an_auth_token(self, monkeypatch):
        seen = capture_urlopen(monkeypatch)
        TwilioSmsSender(
            SID,
            TOKEN,
            api_key_sid=self.KEY_SID,
            api_key_secret=self.KEY_SECRET,
            from_number="+15005550006",
        ).send(PHONE, "code 123456")

        expected = b64encode(f"{self.KEY_SID}:{self.KEY_SECRET}".encode()).decode()
        assert seen["headers"]["authorization"] == f"Basic {expected}"

    def test_the_auth_token_still_works_when_no_key_is_set(self, monkeypatch):
        seen = capture_urlopen(monkeypatch)
        TwilioSmsSender(SID, TOKEN, from_number="+15005550006").send(PHONE, "code")

        expected = b64encode(f"{SID}:{TOKEN}".encode()).decode()
        assert seen["headers"]["authorization"] == f"Basic {expected}"

    @pytest.mark.parametrize(
        "api_key_sid,api_key_secret",
        [(KEY_SID, ""), ("", KEY_SECRET)],
    )
    def test_refuses_half_an_api_key_pair(self, api_key_sid, api_key_secret):
        # Silently falling back to the auth token here would mean a key the
        # operator believes is in use is not, and revoking it would change
        # nothing.
        with pytest.raises(SmsNotConfigured):
            TwilioSmsSender(
                SID,
                TOKEN,
                api_key_sid=api_key_sid,
                api_key_secret=api_key_secret,
                from_number="+15005550006",
            )

    def test_the_api_key_secret_never_reaches_the_logs(self, monkeypatch, caplog):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(sms_module.urllib.request, "urlopen", fake_urlopen)
        with caplog.at_level("DEBUG"), pytest.raises(SmsDeliveryError):
            TwilioSmsSender(
                SID,
                "",
                api_key_sid=self.KEY_SID,
                api_key_secret=self.KEY_SECRET,
                from_number="+15005550006",
            ).send(PHONE, "code")

        assert self.KEY_SECRET not in caplog.text


class TestSenderSelection:
    def test_dev_falls_back_to_the_outbox_sender(self, monkeypatch):
        use_settings(monkeypatch, environment="dev")
        assert isinstance(get_sms_sender(), DevSmsSender)

    def test_prod_without_a_provider_refuses_rather_than_pretending(self, monkeypatch):
        use_settings(
            monkeypatch,
            environment="prod",
            jwt_secret="x" * 40,
            payments_webhook_secret="y" * 40,
            public_base_url="https://api.toolbelt.biz",
        )
        # Returning the dev sender here would be far worse than raising: codes
        # would land in a server log and every signup would appear to succeed.
        with pytest.raises(SmsNotConfigured):
            get_sms_sender()

    def test_configured_credentials_win_even_in_dev(self, monkeypatch):
        # Staging points at a real Twilio trial account while staying non-prod.
        use_settings(
            monkeypatch,
            environment="dev",
            twilio_account_sid=SID,
            twilio_auth_token=TOKEN,
            twilio_from_number="+15005550006",
        )
        assert isinstance(get_sms_sender(), TwilioSmsSender)


class TestTwilioRequest:
    def test_posts_to_the_account_messages_resource_with_basic_auth(self, monkeypatch):
        seen = capture_urlopen(monkeypatch)
        TwilioSmsSender(SID, TOKEN, from_number="+15005550006").send(PHONE, "code 123456")

        assert seen["url"] == f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json"
        assert seen["headers"]["authorization"].startswith("Basic ")
        assert "To=%2B13015550123" in seen["body"]
        assert "From=%2B15005550006" in seen["body"]

    def test_a_messaging_service_wins_over_a_bare_number(self, monkeypatch):
        seen = capture_urlopen(monkeypatch)
        TwilioSmsSender(
            SID,
            TOKEN,
            from_number="+15005550006",
            messaging_service_sid="MG123",
        ).send(PHONE, "code 123456")

        # The service owns the number pool and the opt-out handling; sending
        # from a bare number alongside it bypasses both.
        assert "MessagingServiceSid=MG123" in seen["body"]
        assert "From=" not in seen["body"]

    def test_sends_with_a_timeout(self, monkeypatch):
        seen = capture_urlopen(monkeypatch)
        TwilioSmsSender(SID, TOKEN, from_number="+15005550006", timeout=4.0).send(
            PHONE, "code 123456"
        )
        # A signup request must not hang on a wedged provider connection.
        assert seen["timeout"] == 4.0

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"account_sid": "", "auth_token": TOKEN, "from_number": "+1500"},
            {"account_sid": SID, "auth_token": "", "from_number": "+1500"},
            {"account_sid": SID, "auth_token": TOKEN},
        ],
    )
    def test_refuses_to_construct_half_configured(self, kwargs):
        with pytest.raises(SmsNotConfigured):
            TwilioSmsSender(
                kwargs.pop("account_sid"), kwargs.pop("auth_token"), **kwargs
            )


class TestTwilioFailures:
    def _sender(self):
        return TwilioSmsSender(SID, TOKEN, from_number="+15005550006")

    def test_http_rejection_becomes_a_delivery_error(self, monkeypatch):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 400, "Bad Request", {}, None
            )

        monkeypatch.setattr(sms_module.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(SmsDeliveryError):
            self._sender().send(PHONE, "code 123456")

    def test_unreachable_provider_becomes_a_delivery_error(self, monkeypatch):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(sms_module.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(SmsDeliveryError):
            self._sender().send(PHONE, "code 123456")

    def test_a_timeout_becomes_a_delivery_error(self, monkeypatch):
        def fake_urlopen(request, timeout=None):
            raise TimeoutError("timed out")

        monkeypatch.setattr(sms_module.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(SmsDeliveryError):
            self._sender().send(PHONE, "code 123456")

    @pytest.mark.parametrize("status", ["failed", "undelivered"])
    def test_accepted_then_failed_is_still_a_failure(self, monkeypatch, status):
        # Twilio answers 201 and reports the failure on the resource. Treating a
        # 2xx as success would tell the user a code is on its way that is not.
        capture_urlopen(
            monkeypatch, payload=f'{{"sid":"SM1","status":"{status}","error_code":30006}}'.encode()
        )
        with pytest.raises(SmsDeliveryError):
            self._sender().send(PHONE, "code 123456")

    def test_a_queued_message_is_success(self, monkeypatch):
        capture_urlopen(monkeypatch, payload=b'{"sid":"SM1","status":"queued"}')
        self._sender().send(PHONE, "code 123456")  # must not raise


class TestSecrets:
    def test_mask_phone_keeps_only_the_last_four(self):
        assert mask_phone("+13015550123") == "***0123"
        assert mask_phone("12") == "***"

    def test_the_code_never_reaches_the_logs(self, monkeypatch, caplog):
        capture_urlopen(monkeypatch)
        with caplog.at_level("DEBUG"):
            TwilioSmsSender(SID, TOKEN, from_number="+15005550006").send(
                PHONE, "Your ToolBelt verification code is 424242"
            )

        # Logs are the least-guarded copy of any system, and a code plus a
        # number is the whole secret.
        assert "424242" not in caplog.text
        assert "13015550123" not in caplog.text

    def test_the_auth_token_never_reaches_the_logs(self, monkeypatch, caplog):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(sms_module.urllib.request, "urlopen", fake_urlopen)
        with caplog.at_level("DEBUG"), pytest.raises(SmsDeliveryError):
            TwilioSmsSender(SID, TOKEN, from_number="+15005550006").send(PHONE, "code")

        assert TOKEN not in caplog.text


class TestRequestVerificationRollsBack:
    def test_a_failed_send_does_not_spend_the_resend_cooldown(self, client, monkeypatch):
        """The challenge row must not survive a failed send.

        It is written before the SMS goes out, so without a rollback the user is
        locked out of re-requesting for 60 seconds while holding no code — the
        one state from which there is no way forward.
        """
        register(client, "sms-rollback@example.com")
        headers = login(client, "sms-rollback@example.com")

        class Broken:
            def send(self, phone, message):
                raise SmsDeliveryError("provider down")

        monkeypatch.setattr("app.modules.identity.otp.get_sms_sender", lambda: Broken())
        first = client.post(
            "/me/phone/request-verification", json={"phone": PHONE}, headers=headers
        )
        assert first.status_code == 502, first.text

        # Provider recovers; the retry must be allowed immediately rather than
        # answering 429 for a code that was never delivered.
        monkeypatch.undo()
        DevSmsSender.outbox.clear()
        second = client.post(
            "/me/phone/request-verification", json={"phone": PHONE}, headers=headers
        )
        assert second.status_code == 202, second.text
        assert DevSmsSender.outbox
