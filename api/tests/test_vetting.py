from tests.conftest import login, make_admin, make_worker, post_job, register

from tests.test_phone_otp import _request_code


def _verified_phone(client, headers):
    code = _request_code(client, headers, phone="+13015559876")
    client.post("/me/phone/verify", json={"code": code}, headers=headers)


def test_unvetted_worker_cannot_offer(client):
    register(client, "customer@example.com")
    customer = login(client, "customer@example.com")
    job = post_job(client, customer)

    worker = make_worker(client, "worker@example.com", verified=False)
    resp = client.post(f"/jobs/{job['id']}/offers", json={"price_cents": 5000}, headers=worker)
    assert resp.status_code == 403
    assert "vetting" in resp.json()["detail"].lower()


def test_full_vetting_pipeline(client):
    worker = make_worker(client, "worker@example.com", verified=False)

    # Phone verification is required before submitting.
    assert client.post("/me/worker-profile/submit-vetting", headers=worker).status_code == 409
    _verified_phone(client, worker)

    submitted = client.post("/me/worker-profile/submit-vetting", headers=worker)
    assert submitted.status_code == 200
    assert submitted.json()["vetting_status"] == "pending"

    # Double-submit blocked.
    assert client.post("/me/worker-profile/submit-vetting", headers=worker).status_code == 409

    admin = make_admin(client)
    queue = client.get("/admin/vetting/queue", headers=admin).json()
    assert len(queue) == 1
    worker_id = queue[0]["user_id"]

    decided = client.post(
        f"/admin/workers/{worker_id}/vetting",
        json={"decision": "verified", "note": "ID checked"},
        headers=admin,
    )
    assert decided.status_code == 200
    assert decided.json()["vetting_status"] == "verified"
    assert client.get("/admin/vetting/queue", headers=admin).json() == []

    # Verified worker can now offer.
    register(client, "customer@example.com")
    customer = login(client, "customer@example.com")
    job = post_job(client, customer)
    resp = client.post(f"/jobs/{job['id']}/offers", json={"price_cents": 5000}, headers=worker)
    assert resp.status_code == 201


def test_rejected_worker_can_resubmit(client):
    worker = make_worker(client, "worker@example.com", verified=False)
    _verified_phone(client, worker)
    client.post("/me/worker-profile/submit-vetting", headers=worker)

    admin = make_admin(client)
    worker_id = client.get("/admin/vetting/queue", headers=admin).json()[0]["user_id"]
    client.post(
        f"/admin/workers/{worker_id}/vetting", json={"decision": "rejected"}, headers=admin
    )

    resp = client.post("/me/worker-profile/submit-vetting", headers=worker)
    assert resp.status_code == 200
    assert resp.json()["vetting_status"] == "pending"


def test_admin_endpoints_require_admin(client):
    register(client, "user@example.com")
    headers = login(client, "user@example.com")
    assert client.get("/admin/vetting/queue", headers=headers).status_code == 403
    assert (
        client.post(
            "/admin/workers/1/vetting", json={"decision": "verified"}, headers=headers
        ).status_code
        == 403
    )
    assert client.get("/admin/vetting/queue").status_code == 401
