"""Drive the FastAPI app end-to-end with the emulated stage."""


def test_summary(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["label"] == "test"
    assert body["emulated"] is True
    assert body["connected"] is True


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["connected"] is True
    assert body["state"] == "not_referenced"


def test_state_starts_not_referenced(client):
    r = client.get("/state")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "NOT_REFERENCED"
    assert body["referenced"] is False
    assert body["connected"] is True


def test_move_before_home_conflict(client):
    r = client.post("/move_abs", json={"position": 5.0})
    assert r.status_code == 409
    assert r.json()["error_code"] == "H"


def test_home_and_move_with_wait(client):
    r = client.post("/home", params={"wait": 5})
    assert r.status_code == 202
    assert r.json()["state"] == "READY"

    r = client.post("/move_abs", params={"wait": 5}, json={"position": 5.0})
    assert r.status_code == 202
    body = r.json()
    assert body["state"] == "READY"
    assert body["waited"] is True
    assert abs(body["position"] - 5.0) < 1e-6


def test_move_out_of_range_422(client):
    client.post("/home", params={"wait": 5})
    r = client.post("/move_abs", json={"position": 99.0})
    assert r.status_code == 422
    assert "limits" in r.json()["detail"]


def test_move_rel_with_wait(client):
    client.post("/home", params={"wait": 5})
    r = client.post("/move_rel", params={"wait": 5}, json={"displacement": -3.5})
    assert r.status_code == 202
    assert abs(r.json()["position"] - 10.0) < 1e-6


def test_non_blocking_then_poll(client):
    import time

    client.post("/home", params={"wait": 5})
    # No ?wait: the move is issued and we get 202 immediately, possibly mid-move.
    r = client.post("/move_abs", json={"position": 4.0})
    assert r.status_code == 202
    assert r.json()["waited"] is False
    # Demonstrate the non-blocking contract: poll /state until it settles.
    deadline = time.monotonic() + 5
    while client.get("/state").json()["state"] != "READY":
        assert time.monotonic() < deadline, "move did not settle"
        time.sleep(0.01)
    pos = client.get("/position").json()["position"]
    assert abs(pos - 4.0) < 1e-6


def test_limits_and_info_endpoints(client):
    lim = client.get("/limits").json()
    assert lim["low"] == 0.0 and lim["high"] == 27.0 and lim["units"] == "mm"
    info = client.get("/info").json()
    assert "CONEX-AGP" in info["version"]


def test_disable_enable(client):
    client.post("/home", params={"wait": 5})
    assert client.post("/disable").json()["state"] == "DISABLE"
    assert client.post("/enable").json()["state"] == "READY"
