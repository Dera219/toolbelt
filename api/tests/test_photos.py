from tests.conftest import customer_with_pm, login, make_worker, post_job, register

JPEG = ("kitchen.jpg", b"\xff\xd8\xff\xe0 fake jpeg bytes", "image/jpeg")


def _upload(client, headers, job_id, file=JPEG):
    return client.post(f"/jobs/{job_id}/photos", files={"file": file}, headers=headers)


def test_upload_and_serve_photo(client):
    register(client, "customer@example.com")
    customer = login(client, "customer@example.com")
    job = post_job(client, customer)

    resp = _upload(client, customer, job["id"])
    assert resp.status_code == 201, resp.text
    url = resp.json()["url"]
    assert url.startswith("/uploads/jobs/")

    # Photo appears on the job and the file is actually served.
    fetched = client.get(f"/jobs/{job['id']}", headers=customer).json()
    assert [p["url"] for p in fetched["photos"]] == [url]
    served = client.get(url)
    assert served.status_code == 200
    assert served.content == JPEG[1]


def test_photo_authorization_and_validation(client):
    customer = customer_with_pm(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    job = post_job(client, customer)

    # Only the owner uploads.
    assert _upload(client, worker, job["id"]).status_code == 403
    # Content type allowlist.
    bad = ("notes.txt", b"not an image", "text/plain")
    assert _upload(client, customer, job["id"], bad).status_code == 415

    # Photo cap.
    for _ in range(8):
        assert _upload(client, customer, job["id"]).status_code == 201
    assert _upload(client, customer, job["id"]).status_code == 409


def test_no_photos_on_closed_job(client):
    register(client, "customer@example.com")
    customer = login(client, "customer@example.com")
    job = post_job(client, customer)
    client.post(f"/jobs/{job['id']}/cancel", headers=customer)
    assert _upload(client, customer, job["id"]).status_code == 409
