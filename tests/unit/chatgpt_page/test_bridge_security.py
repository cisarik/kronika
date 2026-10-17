"""Loopback bind, authentication, one-flight ask, and removed routes."""

from __future__ import annotations

import json
import threading
import uuid
from http.client import HTTPConnection

import pytest

from kronika_capture.bridge.server import BridgeRequestHandler, build_state, create_server
from kronika_capture.bridge.store import Store
from kronika_capture.config import (
    API_VERSION,
    APP_NAME,
    BRIDGE_VERSION,
    PROTO_VERSION,
    UPLOAD_UNAVAILABLE,
)

RUNNER_ID = str(uuid.uuid4())
BROWSER_ID = str(uuid.uuid4())
EPOCHS = {}

PROJECT_URL = "https://chatgpt.com/g/g-p-framenest-analysis"


@pytest.fixture
def bridge(tmp_path):
    state = build_state(Store(tmp_path))
    server = create_server("127.0.0.1", 0, state)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield server, state, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(port, method, path, *, token=None, host=None, origin=None, body=None, with_headers=False):
    if path.startswith("/v1/next?"):
        path += f"&runner_id={RUNNER_ID}&epoch={EPOCHS[port]}"
    if path == "/v1/jobs" and body is not None:
        body = {"request_id": str(uuid.uuid4()), **body}
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": host if host is not None else f"127.0.0.1:{port}"}
    if token is not None:
        headers["X-Bridge-Token"] = token
    if origin is not None:
        headers["Origin"] = origin
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    response_headers = dict(response.getheaders())
    connection.close()
    if with_headers:
        return response.status, json.loads(raw) if raw else None, response_headers
    if not raw:
        return response.status, None
    return response.status, json.loads(raw.decode("utf-8"))


def test_bridge_identity_is_canonical_and_protocol_values_stay(bridge) -> None:
    _server, _state, port = bridge
    assert APP_NAME == "kronika-capture"
    assert BRIDGE_VERSION == "0.1.0"
    assert API_VERSION == 1
    assert PROTO_VERSION == 1
    assert BridgeRequestHandler.server_version == f"{APP_NAME}/{BRIDGE_VERSION}"
    assert BridgeRequestHandler.sys_version == ""
    status, health, headers = _request(port, "GET", "/v1/health", with_headers=True)
    assert status == 200
    assert health == {"ok": True, "api": APP_NAME, "api_version": API_VERSION}
    assert headers["Server"] == f"{APP_NAME}/{BRIDGE_VERSION} "


def test_bridge_refuses_non_loopback_bind(tmp_path) -> None:
    state = build_state(Store(tmp_path))
    with pytest.raises(ValueError, match="127.0.0.1"):
        create_server("0.0.0.0", 8765, state)


def test_host_origin_and_token_are_rejected(bridge) -> None:
    _server, state, port = bridge
    status, _payload = _request(port, "GET", "/v1/health")
    assert status == 200

    status, payload = _request(port, "GET", "/v1/health", host="example.test")
    assert status == 403
    assert payload["error"]["step"] == "auth"

    status, payload = _request(port, "GET", "/v1/status", token="nope")
    assert status == 401

    status, payload = _request(
        port,
        "GET",
        "/v1/status",
        token=state.token,
        origin="chrome-extension://abcdefghijklmnop",
    )
    assert status == 403
    assert payload["error"]["message"] == "forbidden Origin"

    status, payload = _request(
        port,
        "GET",
        "/v1/status",
        token=state.token,
        origin=f"http://127.0.0.1:{port}",
    )
    assert status == 200


@pytest.mark.parametrize("origin_kind", ["absent", "empty", "exact", "null", "foreign"])
@pytest.mark.parametrize("authenticated", [False, True])
def test_origin_presence_token_and_cors_contract(bridge, origin_kind, authenticated):
    _server, state, port = bridge
    origin = {"absent": None, "empty": "", "exact": f"http://127.0.0.1:{port}",
              "null": "null", "foreign": "https://example.test"}[origin_kind]
    status, _payload, headers = _request(
        port, "GET", "/v1/status", token=state.token if authenticated else None,
        origin=origin, with_headers=True,
    )
    approved = origin_kind in ("absent", "exact")
    expected = (200 if authenticated else 401) if approved else 403
    assert status == expected
    assert headers.get("Access-Control-Allow-Origin") != "*"
    assert headers.get("Access-Control-Allow-Origin") == (origin if approved and authenticated else None)


def _hello(port, token) -> None:
    status, payload = _request(
        port,
        "POST",
        "/v1/hello",
        token=token,
        body={"proto": 1, "client": "headless", "capabilities": [],
              "runner_id": RUNNER_ID, "browser_session": BROWSER_ID,
              "readiness": {"state": "ready"}},
    )
    assert status == 200
    assert payload["proto"] == 1
    EPOCHS[port] = payload["epoch"]


def test_one_job_is_busy_until_it_finishes(bridge) -> None:
    _server, state, port = bridge
    _hello(port, state.token)
    status, first = _request(
        port,
        "POST",
        "/v1/jobs",
        token=state.token,
        body={"prompt": "name the film"},
    )
    assert status == 200
    status, busy = _request(
        port,
        "POST",
        "/v1/jobs",
        token=state.token,
        body={"prompt": "again"},
    )
    assert status == 409
    assert busy["error"]["code"] == "E_BUSY"
    assert first["job_id"]


def test_ask_text_round_trip_against_fake_executor(bridge) -> None:
    _server, state, port = bridge
    _hello(port, state.token)
    status, created = _request(
        port,
        "POST",
        "/v1/jobs",
        token=state.token,
        body={"prompt": "name the film", "project": None},
    )
    assert status == 200
    job_id = created["job_id"]
    status, offer = _request(
        port,
        "GET",
        "/v1/next?wait=0&client=headless",
        token=state.token,
    )
    assert status == 200
    assert offer["job"]["job_id"] == job_id
    assert offer["job"]["mode"] is None
    assert offer["job"]["kind"] == "ask"
    identity = {key: offer["job"][key] for key in ("runner_id", "offer_id", "epoch")}
    for typ, data in (
        ("status", {"status": "accepted"}),
        ("send_intent", {}),
        ("send_confirmed", {"response_id": str(uuid.uuid4())}),
    ):
        event_status, _ = _request(port, "POST", f"/v1/jobs/{job_id}/events",
                                  token=state.token, body={**identity, "events": [{"type": typ, "data": data}]})
        assert event_status == 200
    status, _done = _request(
        port,
        "POST",
        f"/v1/jobs/{job_id}/result",
        token=state.token,
        body={**identity, "delivery_id": str(uuid.uuid4()), "activity_stopped": True,
              "status": "done", "answer": "The Third Man", "url": None},
    )
    assert status == 200
    status, viewed = _request(port, "GET", f"/v1/jobs/{job_id}", token=state.token)
    assert status == 200
    assert viewed["job"]["status"] == "done"
    assert viewed["job"]["result"]["answer"] == "The Third Man"
    record = state.results.get(viewed["job"]["result_id"])
    assert record is not None
    assert record["text"] == "The Third Man"


def test_removed_modes_and_routes_fail_closed(bridge, tmp_path) -> None:
    _server, state, port = bridge
    _hello(port, state.token)
    expectations = {
        "web_search": "E_WEB_SEARCH_UNAVAILABLE",
        "deep_research": "E_DEEP_RESEARCH_UNAVAILABLE",
        "search": "E_INTERNAL",
        "verify": "E_INTERNAL",
    }
    for mode, code in expectations.items():
        status, payload = _request(
            port,
            "POST",
            "/v1/jobs",
            token=state.token,
            body={"prompt": "hello", "mode": mode},
        )
        assert status == 400
        assert payload["error"]["code"] == code
        assert "nothing was queued" in payload["error"]["message"]

    status, payload = _request(
        port,
        "POST",
        "/v1/jobs",
        token=state.token,
        body={"prompt": "hello", "kind": "ingest"},
    )
    assert status == 400
    assert payload["error"]["code"] == "E_INTERNAL"

    status, payload = _request(
        port,
        "POST",
        "/v1/jobs",
        token=state.token,
        body={"prompt": "hello", "files": ["fid"]},
    )
    assert status == 400
    assert payload["error"]["code"] == "E_UPLOAD_FAILED"
    assert payload["error"]["message"] == UPLOAD_UNAVAILABLE

    for path in ("/v1/ingest", "/v1/author", "/v1/capture-auth", "/s/secret/r/1"):
        status, payload = _request(port, "POST", path, token=state.token, body={})
        assert status == 404, path
        assert payload["error"]["message"] == "unknown path"

    status, payload = _request(port, "GET", "/v1/files/fid", token=state.token)
    assert status == 501
    assert payload["error"]["message"] == UPLOAD_UNAVAILABLE

    state.store.save_config(
        {"projects": {"analysis": {"name": "analysis", "url": PROJECT_URL}}}
    )
    status, created = _request(
        port,
        "POST",
        "/v1/jobs",
        token=state.token,
        body={"prompt": "contained", "project": "analysis"},
    )
    assert status == 200
    assert created["job_id"]

def test_readiness_resume_and_request_replay_are_authenticated(bridge):
    _server, state, port = bridge
    status, health = _request(port, "GET", "/v1/health")
    assert status == 200
    assert set(health) == {"ok", "api", "api_version"}
    status, _ = _request(port, "POST", "/v1/resume", body={})
    assert status == 401
    status, _ = _request(port, "GET", "/v1/status")
    assert status == 401
    _hello(port, state.token)
    request_id = str(uuid.uuid4())
    status, created = _request(port, "POST", "/v1/jobs", token=state.token,
                               body={"request_id": request_id, "prompt": "PRIVATE CONTENT"})
    assert status == 200 and created["request_id"] == request_id and created["status"] == "queued"
    status, replay = _request(port, "POST", "/v1/jobs", token=state.token,
                              body={"request_id": request_id, "prompt": "PRIVATE CONTENT"})
    assert status == 200 and replay["job_id"] == created["job_id"]
    status, conflict = _request(port, "POST", "/v1/jobs", token=state.token,
                                 body={"request_id": request_id, "prompt": "different"})
    assert status == 409 and conflict["error"]["code"] == "E_IDEMPOTENCY_CONFLICT"
    status, metadata = _request(port, "GET", "/v1/status", token=state.token)
    assert status == 200
    assert "PRIVATE CONTENT" not in json.dumps(metadata)
    for _ in range(2):
        status, cancelled = _request(port, "POST", f"/v1/jobs/{created['job_id']}/cancel",
                                     token=state.token, body={})
        assert status == 200 and cancelled["status"] == "cancelled"
    status, invalid = _request(port, "POST", "/v1/jobs", token=state.token,
                               body={"request_id": None, "prompt": "invalid"})
    assert status == 400
