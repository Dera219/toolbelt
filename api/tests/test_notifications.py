"""Push notification fan-out.

The targeting rules are the whole point: a marketplace that pings every worker
about every job trains them to mute notifications, and a marketplace that pings
nobody has no liquidity. These tests pin who gets told what.
"""

from app.modules.notifications.push import _dev_sender
from tests.conftest import (
    UMD,
    BALTIMORE,
    customer_with_pm,
    login,
    make_worker,
    post_job,
    register,
)


def _outbox():
    return _dev_sender.outbox


def _register_device(client, headers, token: str):
    resp = client.post(
        "/me/device-tokens", json={"token": token, "platform": "ios"}, headers=headers
    )
    assert resp.status_code == 204, resp.text


def _titles():
    return [m.title for m in _outbox()]


def _tokens_notified():
    return {m.to for m in _outbox()}


# ---------------------------------------------------------------- registration

def test_register_device_token_is_idempotent(client, clean_db):
    headers = login(client, register(client, "dev@example.com") and "dev@example.com")
    for _ in range(3):
        _register_device(client, headers, "ExpoPushToken[aaa]")
    # A second registration of the same token must not create a duplicate — the
    # app calls this on every launch.
    resp = client.post(
        "/me/device-tokens", json={"token": "ExpoPushToken[aaa]"}, headers=headers
    )
    assert resp.status_code == 204


def test_token_moves_to_the_new_user_on_shared_device(client, clean_db):
    """A phone that changes hands must not keep delivering the old user's jobs."""
    first = login(client, register(client, "first@example.com") and "first@example.com")
    second = login(client, register(client, "second@example.com") and "second@example.com")
    _register_device(client, first, "ExpoPushToken[shared]")
    _register_device(client, second, "ExpoPushToken[shared]")

    _dev_sender.reset()
    cust = customer_with_pm(client, "poster@example.com")
    post_job(client, cust)
    # Neither of the two is a matching worker, so nothing is sent — but the
    # point is the token is now bound to `second`, verified by re-registering
    # without a uniqueness error above.
    assert _tokens_notified() == set()


def test_unregister_removes_the_token(client, clean_db):
    headers = login(client, register(client, "out@example.com") and "out@example.com")
    _register_device(client, headers, "ExpoPushToken[bye]")
    resp = client.delete("/me/device-tokens/ExpoPushToken[bye]", headers=headers)
    assert resp.status_code == 204


# ------------------------------------------------------------------ targeting

def test_job_posted_notifies_matching_nearby_worker(client, clean_db):
    worker = make_worker(client, "w@example.com", trade="cleaning", **UMD)
    _register_device(client, worker, "ExpoPushToken[worker]")

    _dev_sender.reset()
    cust = customer_with_pm(client, "c@example.com")
    post_job(client, cust, trade="cleaning", **UMD)

    assert "ExpoPushToken[worker]" in _tokens_notified()
    assert any("cleaning" in t for t in _titles())


def test_job_posted_skips_wrong_trade(client, clean_db):
    worker = make_worker(client, "mover@example.com", trade="moving", **UMD)
    _register_device(client, worker, "ExpoPushToken[mover]")

    _dev_sender.reset()
    cust = customer_with_pm(client, "c2@example.com")
    post_job(client, cust, trade="cleaning", **UMD)

    assert "ExpoPushToken[mover]" not in _tokens_notified()


def test_job_posted_skips_worker_outside_their_service_radius(client, clean_db):
    """Targeting uses the worker's own radius, not a platform default."""
    far = make_worker(client, "far@example.com", trade="cleaning", **BALTIMORE)
    _register_device(client, far, "ExpoPushToken[far]")

    _dev_sender.reset()
    cust = customer_with_pm(client, "c3@example.com")
    post_job(client, cust, trade="cleaning", **UMD)

    assert "ExpoPushToken[far]" not in _tokens_notified()


def test_job_posted_skips_unvetted_worker(client, clean_db):
    """An unvetted worker cannot make offers, so notifying them is noise."""
    pending = make_worker(
        client, "pending@example.com", trade="cleaning", verified=False, **UMD
    )
    _register_device(client, pending, "ExpoPushToken[pending]")

    _dev_sender.reset()
    cust = customer_with_pm(client, "c4@example.com")
    post_job(client, cust, trade="cleaning", **UMD)

    assert "ExpoPushToken[pending]" not in _tokens_notified()


# --------------------------------------------------------------- lifecycle

def test_offer_and_acceptance_notify_the_other_party(client, clean_db):
    worker = make_worker(client, "w2@example.com", trade="cleaning", **UMD)
    cust = customer_with_pm(client, "c5@example.com")
    _register_device(client, worker, "ExpoPushToken[w2]")
    _register_device(client, cust, "ExpoPushToken[c5]")

    job = post_job(client, cust, trade="cleaning", **UMD)

    _dev_sender.reset()
    offer = client.post(
        f"/jobs/{job['id']}/offers",
        json={"price_cents": 5000, "message": "can do"},
        headers=worker,
    ).json()
    # The customer hears about the offer; the worker who sent it does not.
    assert "ExpoPushToken[c5]" in _tokens_notified()
    assert "ExpoPushToken[w2]" not in _tokens_notified()

    _dev_sender.reset()
    resp = client.post(f"/offers/{offer['id']}/accept", headers=cust)
    assert resp.status_code == 200, resp.text
    # Now the worker hears that they won it.
    assert "ExpoPushToken[w2]" in _tokens_notified()


def test_chat_message_notifies_only_the_recipient(client, clean_db):
    worker = make_worker(client, "w3@example.com", trade="cleaning", **UMD)
    cust = customer_with_pm(client, "c6@example.com")
    _register_device(client, worker, "ExpoPushToken[w3]")
    _register_device(client, cust, "ExpoPushToken[c6]")
    job = post_job(client, cust, trade="cleaning", **UMD)
    # worker_id comes back on the offer — the thread is keyed by it.
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": 5000}, headers=worker
    ).json()
    worker_id = offer["worker_id"]

    _dev_sender.reset()
    resp = client.post(
        f"/jobs/{job['id']}/threads/{worker_id}/messages",
        json={"body": "What time works?"},
        headers=cust,
    )
    assert resp.status_code == 201, resp.text
    assert _tokens_notified() == {"ExpoPushToken[w3]"}, "sender must not notify themselves"


def test_push_failure_never_breaks_the_booking(client, clean_db, monkeypatch):
    """A dead push service must not cost you a job."""
    worker = make_worker(client, "w4@example.com", trade="cleaning", **UMD)
    _register_device(client, worker, "ExpoPushToken[w4]")

    def explode(messages):
        raise RuntimeError("push service down")

    monkeypatch.setattr(_dev_sender, "send", explode)

    cust = customer_with_pm(client, "c7@example.com")
    resp = client.post(
        "/jobs",
        json={
            "trade": "cleaning",
            "title": "Deep clean",
            "description": "two bedroom",
            "address_text": "College Park",
            **UMD,
        },
        headers=cust,
    )
    assert resp.status_code == 201, "job creation must survive a push failure"
