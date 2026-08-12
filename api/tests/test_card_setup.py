"""Card collection: native payment sheet and the hosted web fallback.

The point of both flows is that card details go from the device to Stripe
directly — this server only ever learns a payment-method reference.
"""

from app.modules.payments.provider import _fake_provider
from tests.conftest import login, register


def _user(client):
    register(client, "buyer@example.com")
    return login(client, "buyer@example.com")


def test_native_setup_returns_sheet_parameters(client):
    headers = _user(client)
    resp = client.post("/me/billing/card-setup", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["setup_intent_client_secret"]
    assert body["customer_ephemeral_key_secret"]
    assert body["customer_ref"].startswith("cus_")


def test_web_setup_returns_hosted_url(client):
    headers = _user(client)
    resp = client.post(
        "/me/billing/card-setup-session",
        json={"return_url": "http://localhost:8081"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("https://checkout.fake/")


def test_confirm_records_the_card_the_provider_actually_has(client):
    """The client never tells us which card was saved — we ask the provider."""
    headers = _user(client)
    client.post("/me/billing/card-setup", headers=headers)

    # Simulate the sheet completing: the card is now attached at the provider.
    customer_ref = _fake_provider.customers[-1]
    _fake_provider.attached.append((customer_ref, "pm_saved_by_sheet"))

    resp = client.post("/me/billing/confirm-card", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["default_payment_method_ref"] == "pm_saved_by_sheet"


def test_confirm_without_a_saved_card_is_rejected(client):
    headers = _user(client)
    client.post("/me/billing/card-setup", headers=headers)
    resp = client.post("/me/billing/confirm-card", headers=headers)
    assert resp.status_code == 409
    assert "card" in resp.json()["detail"].lower()


def test_confirm_before_setup_is_rejected(client):
    headers = _user(client)
    assert client.post("/me/billing/confirm-card", headers=headers).status_code == 404


def test_card_setup_requires_auth(client):
    assert client.post("/me/billing/card-setup").status_code == 401
    assert client.post("/me/billing/confirm-card").status_code == 401


def test_setup_reuses_one_customer_per_user(client):
    """A second attempt must not create a duplicate customer, or saved cards
    scatter across customers and the charge later finds none."""
    headers = _user(client)
    client.post("/me/billing/card-setup", headers=headers)
    first = _fake_provider.customers[-1]
    client.post("/me/billing/card-setup", headers=headers)
    assert _fake_provider.customers[-1] == first
    assert len(_fake_provider.customers) == 1


def test_saved_card_can_then_book_a_job(client):
    """The reason any of this exists: a saved card authorizes on acceptance."""
    from tests.conftest import make_worker, post_job

    headers = _user(client)
    client.post("/me/billing/card-setup", headers=headers)
    _fake_provider.attached.append((_fake_provider.customers[-1], "pm_from_sheet"))
    client.post("/me/billing/confirm-card", headers=headers)

    worker = make_worker(client, "pro@example.com")
    job = post_job(client, headers)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": 9000}, headers=worker
    ).json()
    assert client.post(f"/offers/{offer['id']}/accept", headers=headers).status_code == 200
    assert _fake_provider.authorizations[-1]["amount_cents"] == 9000
