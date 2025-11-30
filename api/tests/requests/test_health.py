def test_liveness_endpoint(client):
    response = client.get("/liveness")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"


def test_up_endpoint(client):
    response = client.get("/up")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"

