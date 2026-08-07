from app.modules.chat.service import MASK, mask_contact_info
from tests.conftest import customer_with_pm, login, make_worker, post_job, register


def _setup_with_offer(client):
    customer = customer_with_pm(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    job = post_job(client, customer)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": 10000}, headers=worker
    ).json()
    worker_id = offer["worker_id"]
    return job, offer, customer, worker, worker_id


def test_mask_patterns():
    masked = mask_contact_info("call me at 301-555-0123 or (240) 555 9876, or mail a@b.com")
    assert "301" not in masked and "9876" not in masked and "a@b.com" not in masked
    assert masked.count(MASK) == 3
    # Normal text with small numbers survives.
    assert mask_contact_info("3 bedrooms, 2 baths, unit 12B") == "3 bedrooms, 2 baths, unit 12B"


def test_contact_masked_until_booked_then_clear(client):
    job, offer, customer, worker, worker_id = _setup_with_offer(client)
    thread = f"/jobs/{job['id']}/threads/{worker_id}/messages"

    sent = client.post(
        thread, json={"body": "Text me at +13015550123 before you accept"}, headers=worker
    ).json()
    assert "+13015550123" not in sent["body"]
    assert MASK in sent["body"]

    client.post(f"/offers/{offer['id']}/accept", headers=customer)

    sent = client.post(thread, json={"body": "Booked! Call +13015550123"}, headers=worker).json()
    assert "+13015550123" in sent["body"]

    history = client.get(thread, headers=customer).json()
    assert len(history) == 2
    assert MASK in history[0]["body"]  # pre-booking message stays masked forever


def test_thread_authorization(client):
    job, offer, customer, worker, worker_id = _setup_with_offer(client)
    thread = f"/jobs/{job['id']}/threads/{worker_id}/messages"

    # Stranger and uninvolved worker are shut out.
    register(client, "stranger@example.com")
    stranger = login(client, "stranger@example.com")
    assert client.post(thread, json={"body": "hi"}, headers=stranger).status_code == 403
    assert client.get(thread, headers=stranger).status_code == 403

    rival = make_worker(client, "rival@example.com")
    rival_thread = f"/jobs/{job['id']}/threads/{worker_id + 1}/messages"
    # A worker with no offer on the job has no thread.
    assert client.post(rival_thread, json={"body": "hi"}, headers=rival).status_code == 403

    # Both real parties can talk.
    assert client.post(thread, json={"body": "When can you start?"}, headers=customer).status_code == 201
    assert client.post(thread, json={"body": "Tomorrow 9am"}, headers=worker).status_code == 201
    assert len(client.get(thread, headers=worker).json()) == 2


def test_chat_pagination(client):
    job, offer, customer, worker, worker_id = _setup_with_offer(client)
    thread = f"/jobs/{job['id']}/threads/{worker_id}/messages"
    ids = [
        client.post(thread, json={"body": f"msg {i}"}, headers=customer).json()["id"]
        for i in range(5)
    ]
    newest_two = client.get(thread, params={"limit": 2}, headers=worker).json()
    assert [m["id"] for m in newest_two] == ids[-2:]
    older = client.get(
        thread, params={"limit": 2, "before_id": newest_two[0]["id"]}, headers=worker
    ).json()
    assert [m["id"] for m in older] == ids[1:3]
