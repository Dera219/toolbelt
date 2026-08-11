from datetime import timedelta

from app.core.db import SessionLocal
from app.modules.identity.models import utcnow
from app.modules.jobs.models import JobEvent, JobStatus
from tests.conftest import customer_with_pm, login, make_admin, make_worker, post_job, register


def _completed_job(client, price_cents=10000):
    customer = customer_with_pm(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    job = post_job(client, customer)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": price_cents}, headers=worker
    ).json()
    client.post(f"/offers/{offer['id']}/accept", headers=customer)
    client.post(f"/jobs/{job['id']}/start", headers=worker)
    client.post(f"/jobs/{job['id']}/complete", headers=worker)
    return job, customer, worker


def test_open_dispute_freezes_job(client):
    job, customer, worker = _completed_job(client)

    resp = client.post(
        f"/jobs/{job['id']}/dispute",
        json={"reason": "quality", "detail": "Bathroom was skipped entirely."},
        headers=customer,
    )
    assert resp.status_code == 201, resp.text
    dispute = resp.json()
    assert dispute["status"] == "open"
    assert dispute["reason"] == "quality"
    assert dispute["outcome"] is None
    assert dispute["refunded_cents"] == 0

    assert client.get(f"/jobs/{job['id']}", headers=customer).json()["status"] == "disputed"
    # Both parties can read it.
    assert client.get(f"/jobs/{job['id']}/dispute", headers=worker).json()["id"] == dispute["id"]


def test_full_refund_resolution_restores_status_and_moves_money(client):
    job, customer, worker = _completed_job(client)
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 8500

    client.post(
        f"/jobs/{job['id']}/dispute", json={"reason": "work_not_done"}, headers=customer
    )
    admin = make_admin(client)

    queue = client.get("/admin/disputes", headers=admin).json()
    assert len(queue) == 1

    resolved = client.post(
        f"/admin/disputes/{queue[0]['id']}/resolve",
        json={"outcome": "full_refund", "note": "Photos confirm no work done."},
        headers=admin,
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["refunded_cents"] == 10000
    assert resolved.json()["status"] == "resolved"

    # Job returns to the status it held before the dispute.
    assert client.get(f"/jobs/{job['id']}", headers=customer).json()["status"] == "completed"
    payment = client.get(f"/jobs/{job['id']}/payment", headers=customer).json()
    assert payment["status"] == "refunded"
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 0
    assert client.get("/admin/ledger/trial-balance", headers=admin).json()["balanced"] is True
    assert client.get("/admin/disputes", headers=admin).json() == []


def test_partial_refund_splits_proportionally(client):
    job, customer, worker = _completed_job(client)
    client.post(f"/jobs/{job['id']}/dispute", json={"reason": "quality"}, headers=customer)
    admin = make_admin(client)
    dispute_id = client.get("/admin/disputes", headers=admin).json()[0]["id"]

    resolved = client.post(
        f"/admin/disputes/{dispute_id}/resolve",
        json={"outcome": "partial_refund", "refund_cents": 4000},
        headers=admin,
    ).json()
    assert resolved["refunded_cents"] == 4000

    # Worker gives back their 85% share of the refunded amount.
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 8500 - 3400
    assert client.get("/admin/ledger/trial-balance", headers=admin).json()["balanced"] is True


def test_dismissed_dispute_refunds_nothing(client):
    job, customer, worker = _completed_job(client)
    client.post(f"/jobs/{job['id']}/dispute", json={"reason": "damage"}, headers=customer)
    admin = make_admin(client)
    dispute_id = client.get("/admin/disputes", headers=admin).json()[0]["id"]

    resolved = client.post(
        f"/admin/disputes/{dispute_id}/resolve",
        json={"outcome": "dismissed", "note": "No evidence of damage."},
        headers=admin,
    ).json()
    assert resolved["refunded_cents"] == 0
    assert resolved["outcome"] == "dismissed"
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 8500
    assert client.get(f"/jobs/{job['id']}", headers=customer).json()["status"] == "completed"


def test_partial_refund_requires_amount(client):
    job, customer, _ = _completed_job(client)
    client.post(f"/jobs/{job['id']}/dispute", json={"reason": "quality"}, headers=customer)
    admin = make_admin(client)
    dispute_id = client.get("/admin/disputes", headers=admin).json()[0]["id"]

    resp = client.post(
        f"/admin/disputes/{dispute_id}/resolve", json={"outcome": "partial_refund"}, headers=admin
    )
    assert resp.status_code == 422


def test_worker_can_dispute_too(client):
    """Disputes are symmetric — a worker can contest a customer."""
    job, customer, worker = _completed_job(client)
    resp = client.post(
        f"/jobs/{job['id']}/dispute",
        json={"reason": "overcharged", "detail": "Job was bigger than described."},
        headers=worker,
    )
    assert resp.status_code == 201
    assert resp.json()["against_id"] != resp.json()["opened_by"]


def test_dispute_authorization(client):
    job, customer, worker = _completed_job(client)
    register(client, "stranger@example.com")
    stranger = login(client, "stranger@example.com")

    assert (
        client.post(
            f"/jobs/{job['id']}/dispute", json={"reason": "quality"}, headers=stranger
        ).status_code
        == 403
    )
    assert client.get(f"/jobs/{job['id']}/dispute", headers=stranger).status_code == 403

    # Non-admins cannot see the queue or resolve.
    assert client.get("/admin/disputes", headers=customer).status_code == 403
    client.post(f"/jobs/{job['id']}/dispute", json={"reason": "quality"}, headers=customer)
    assert (
        client.post(
            "/admin/disputes/1/resolve", json={"outcome": "dismissed"}, headers=worker
        ).status_code
        == 403
    )


def test_one_open_dispute_per_job(client):
    job, customer, worker = _completed_job(client)
    assert (
        client.post(
            f"/jobs/{job['id']}/dispute", json={"reason": "quality"}, headers=customer
        ).status_code
        == 201
    )
    # Second attempt, from either side, is refused while one is open.
    assert (
        client.post(
            f"/jobs/{job['id']}/dispute", json={"reason": "damage"}, headers=worker
        ).status_code
        == 409
    )


def test_open_job_cannot_be_disputed(client):
    customer = customer_with_pm(client, "customer@example.com")
    make_worker(client, "worker@example.com")
    job = post_job(client, customer)
    resp = client.post(f"/jobs/{job['id']}/dispute", json={"reason": "no_show"}, headers=customer)
    assert resp.status_code == 409


def test_dispute_window_closes(client):
    job, customer, worker = _completed_job(client)

    # Age the completion event past the 3-day window.
    with SessionLocal() as db:
        event = (
            db.query(JobEvent)
            .filter(JobEvent.job_id == job["id"], JobEvent.to_status == JobStatus.COMPLETED.value)
            .one()
        )
        event.created_at = utcnow() - timedelta(days=4)
        db.commit()

    resp = client.post(f"/jobs/{job['id']}/dispute", json={"reason": "quality"}, headers=customer)
    assert resp.status_code == 409
    assert "window" in resp.json()["detail"].lower()


def test_dispute_during_work_restores_in_progress(client):
    customer = customer_with_pm(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    job = post_job(client, customer)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": 10000}, headers=worker
    ).json()
    client.post(f"/offers/{offer['id']}/accept", headers=customer)
    client.post(f"/jobs/{job['id']}/start", headers=worker)

    client.post(f"/jobs/{job['id']}/dispute", json={"reason": "unsafe"}, headers=customer)
    admin = make_admin(client)
    dispute_id = client.get("/admin/disputes", headers=admin).json()[0]["id"]

    # Payment is still only authorized, so a refund is impossible — say so clearly.
    refund_attempt = client.post(
        f"/admin/disputes/{dispute_id}/resolve", json={"outcome": "full_refund"}, headers=admin
    )
    assert refund_attempt.status_code == 409

    client.post(
        f"/admin/disputes/{dispute_id}/resolve", json={"outcome": "dismissed"}, headers=admin
    )
    assert client.get(f"/jobs/{job['id']}", headers=customer).json()["status"] == "in_progress"
