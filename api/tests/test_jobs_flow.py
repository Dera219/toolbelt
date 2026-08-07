from tests.conftest import (
    BALTIMORE,
    DC,
    UMD,
    customer_with_pm,
    login,
    make_worker,
    post_job,
    register,
)


def _setup(client):
    customer = customer_with_pm(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    return customer, worker


def test_full_marketplace_loop(client):
    """post → nearby → offer → accept → start → complete, with sibling-offer expiry."""
    customer, worker = _setup(client)
    rival = make_worker(client, "rival@example.com")
    job = post_job(client, customer)

    nearby = client.get("/jobs/nearby", params=UMD, headers=worker).json()
    assert [j["id"] for j in nearby] == [job["id"]]
    assert nearby[0]["distance_km"] == 0

    offer = client.post(
        f"/jobs/{job['id']}/offers",
        json={"price_cents": 11000, "message": "Can start today"},
        headers=worker,
    ).json()
    rival_offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": 9000}, headers=rival
    ).json()

    accepted = client.post(f"/offers/{offer['id']}/accept", headers=customer)
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"

    offers = client.get(f"/jobs/{job['id']}/offers", headers=customer).json()
    statuses = {o["id"]: o["status"] for o in offers}
    assert statuses[offer["id"]] == "accepted"
    assert statuses[rival_offer["id"]] == "expired"

    job_now = client.get(f"/jobs/{job['id']}", headers=customer).json()
    assert job_now["status"] == "assigned"

    assert client.post(f"/jobs/{job['id']}/start", headers=worker).json()["status"] == "in_progress"
    assert (
        client.post(f"/jobs/{job['id']}/complete", headers=customer).json()["status"] == "completed"
    )

    # Assigned job no longer appears in search.
    assert client.get("/jobs/nearby", params=UMD, headers=rival).json() == []


def test_nearby_respects_radius_and_trade(client):
    customer, worker = _setup(client)
    near = post_job(client, customer, title="Near job")
    post_job(client, customer, lat=BALTIMORE["lat"], lng=BALTIMORE["lng"], title="Far job")
    post_job(client, customer, trade="moving", title="Wrong trade")

    within_20 = client.get(
        "/jobs/nearby", params={**UMD, "radius_km": 20, "trade": "cleaning"}, headers=worker
    ).json()
    assert [j["id"] for j in within_20] == [near["id"]]

    from_dc = client.get(
        "/jobs/nearby", params={**DC, "radius_km": 60, "trade": "cleaning"}, headers=worker
    ).json()
    assert len(from_dc) == 2
    assert from_dc[0]["title"] == "Near job"  # sorted by distance


def test_offer_authorization_rules(client):
    customer, worker = _setup(client)
    job = post_job(client, customer)

    # Customer without a worker profile cannot offer.
    resp = client.post(f"/jobs/{job['id']}/offers", json={"price_cents": 5000}, headers=customer)
    assert resp.status_code == 403

    # Wrong-trade worker cannot offer.
    mover = make_worker(client, "mover@example.com", trade="moving")
    resp = client.post(f"/jobs/{job['id']}/offers", json={"price_cents": 5000}, headers=mover)
    assert resp.status_code == 403

    # Double offer rejected.
    ok = client.post(f"/jobs/{job['id']}/offers", json={"price_cents": 5000}, headers=worker)
    assert ok.status_code == 201
    dup = client.post(f"/jobs/{job['id']}/offers", json={"price_cents": 4000}, headers=worker)
    assert dup.status_code == 409


def test_only_job_owner_accepts_and_only_worker_starts(client):
    customer, worker = _setup(client)
    job = post_job(client, customer)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": 5000}, headers=worker
    ).json()

    register(client, "stranger@example.com")
    stranger = login(client, "stranger@example.com")

    assert client.post(f"/offers/{offer['id']}/accept", headers=stranger).status_code == 403
    assert client.post(f"/offers/{offer['id']}/accept", headers=worker).status_code == 403

    # Cannot start before assignment.
    assert client.post(f"/jobs/{job['id']}/start", headers=worker).status_code == 403

    client.post(f"/offers/{offer['id']}/accept", headers=customer)

    # Customer cannot start; stranger cannot start.
    assert client.post(f"/jobs/{job['id']}/start", headers=customer).status_code == 403
    assert client.post(f"/jobs/{job['id']}/start", headers=stranger).status_code == 403


def test_state_machine_blocks_invalid_transitions(client):
    customer, worker = _setup(client)
    job = post_job(client, customer)

    # Cannot complete an open job.
    assert client.post(f"/jobs/{job['id']}/complete", headers=customer).status_code in (403, 409)

    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": 5000}, headers=worker
    ).json()
    client.post(f"/offers/{offer['id']}/accept", headers=customer)
    client.post(f"/jobs/{job['id']}/start", headers=worker)
    client.post(f"/jobs/{job['id']}/complete", headers=worker)

    # Completed is terminal.
    assert client.post(f"/jobs/{job['id']}/cancel", headers=customer).status_code == 409
    assert client.post(f"/jobs/{job['id']}/start", headers=worker).status_code == 409

    # Accepting anything on a closed job fails.
    assert client.post(f"/offers/{offer['id']}/accept", headers=customer).status_code == 409


def test_cancel_rules(client):
    customer, worker = _setup(client)
    job = post_job(client, customer)

    # Worker (non-party while open) cannot cancel an open job.
    assert client.post(f"/jobs/{job['id']}/cancel", headers=worker).status_code == 403

    resp = client.post(f"/jobs/{job['id']}/cancel", headers=customer)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    # Cancelled jobs cannot receive offers.
    resp = client.post(f"/jobs/{job['id']}/offers", json={"price_cents": 5000}, headers=worker)
    assert resp.status_code == 409


def test_pagination_limits_and_caps(client):
    customer, worker = _setup(client)
    near = post_job(client, customer, title="Nearest")
    post_job(client, customer, lat=DC["lat"], lng=DC["lng"], title="Farther")

    limited = client.get(
        "/jobs/nearby", params={**UMD, "radius_km": 60, "limit": 1}, headers=worker
    ).json()
    assert [j["id"] for j in limited] == [near["id"]]  # limit keeps the nearest

    assert (
        client.get("/jobs/nearby", params={**UMD, "limit": 500}, headers=worker).status_code == 422
    )

    page1 = client.get("/jobs/mine", params={"limit": 1}, headers=customer).json()
    page2 = client.get("/jobs/mine", params={"limit": 1, "offset": 1}, headers=customer).json()
    assert len(page1) == len(page2) == 1
    assert page1[0]["id"] != page2[0]["id"]


def test_regulated_trade_not_bookable_yet(client):
    register(client, "customer@example.com")
    customer = login(client, "customer@example.com")
    resp = client.post(
        "/jobs",
        json={
            "trade": "electrical",
            "title": "Rewire panel",
            "lat": UMD["lat"],
            "lng": UMD["lng"],
            "address_text": "College Park, MD",
        },
        headers=customer,
    )
    assert resp.status_code == 422
