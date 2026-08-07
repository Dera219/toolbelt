from datetime import timedelta

from app.core.db import SessionLocal
from app.modules.identity.models import utcnow
from app.modules.reputation.models import Rating
from tests.conftest import customer_with_pm, login, make_worker, post_job, register


def _completed_job(client):
    customer = customer_with_pm(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    job = post_job(client, customer)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": 10000}, headers=worker
    ).json()
    client.post(f"/offers/{offer['id']}/accept", headers=customer)
    client.post(f"/jobs/{job['id']}/start", headers=worker)
    client.post(f"/jobs/{job['id']}/complete", headers=worker)
    return job, customer, worker


def test_double_blind_reveal(client):
    job, customer, worker = _completed_job(client)

    resp = client.post(
        f"/jobs/{job['id']}/ratings", json={"stars": 5, "comment": "Great work"}, headers=customer
    )
    assert resp.status_code == 201

    # Customer sees own rating; worker sees that one was submitted but not its content.
    view = client.get(f"/jobs/{job['id']}/ratings", headers=worker).json()
    assert view["mine"] is None
    assert view["other"] is None
    assert view["other_submitted"] is True

    client.post(
        f"/jobs/{job['id']}/ratings", json={"stars": 4, "comment": "Fair customer"}, headers=worker
    )

    # Both submitted → both revealed, both directions.
    for headers, other_stars in ((customer, 4), (worker, 5)):
        view = client.get(f"/jobs/{job['id']}/ratings", headers=headers).json()
        assert view["mine"] is not None
        assert view["other"]["stars"] == other_stars


def test_reveal_after_window_without_counterparty(client):
    job, customer, worker = _completed_job(client)
    client.post(f"/jobs/{job['id']}/ratings", json={"stars": 5}, headers=customer)

    view = client.get(f"/jobs/{job['id']}/ratings", headers=worker).json()
    assert view["other"] is None

    # Age the rating past the 14-day window directly in the DB.
    with SessionLocal() as db:
        rating = db.query(Rating).one()
        rating.created_at = utcnow() - timedelta(days=15)
        db.commit()

    view = client.get(f"/jobs/{job['id']}/ratings", headers=worker).json()
    assert view["other"]["stars"] == 5


def test_worker_aggregate_updates(client):
    job, customer, worker = _completed_job(client)
    client.post(f"/jobs/{job['id']}/ratings", json={"stars": 4}, headers=customer)

    profile = client.get("/me/worker-profile", headers=worker).json()
    assert profile["rating_avg"] == 4.0
    assert profile["jobs_completed"] == 1


def test_rating_guards(client):
    job, customer, worker = _completed_job(client)

    # Stranger cannot rate or view.
    register(client, "stranger@example.com")
    stranger = login(client, "stranger@example.com")
    assert (
        client.post(f"/jobs/{job['id']}/ratings", json={"stars": 1}, headers=stranger).status_code
        == 403
    )
    assert client.get(f"/jobs/{job['id']}/ratings", headers=stranger).status_code == 403

    # Duplicate rating rejected; stars bounds enforced.
    client.post(f"/jobs/{job['id']}/ratings", json={"stars": 5}, headers=customer)
    assert (
        client.post(f"/jobs/{job['id']}/ratings", json={"stars": 3}, headers=customer).status_code
        == 409
    )
    assert (
        client.post(f"/jobs/{job['id']}/ratings", json={"stars": 6}, headers=worker).status_code
        == 422
    )


def test_cannot_rate_uncompleted_job(client):
    register(client, "customer@example.com")
    customer = login(client, "customer@example.com")
    make_worker(client, "worker@example.com")
    job = post_job(client, customer)
    resp = client.post(f"/jobs/{job['id']}/ratings", json={"stars": 5}, headers=customer)
    assert resp.status_code == 409
