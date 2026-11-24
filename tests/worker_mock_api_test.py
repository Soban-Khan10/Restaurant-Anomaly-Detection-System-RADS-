"""scripts/mock_api.py: token check, contract bodies, last-100 call log."""
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shared.contract import INTERNAL_HEADER, PATH_HEARTBEAT, PATH_INCIDENT_CLOSED, PATH_INCIDENT_NEW

TOKEN = "secret-token"


@pytest.fixture
def client():
    path = Path(__file__).resolve().parents[1] / "scripts" / "mock_api.py"
    spec = importlib.util.spec_from_file_location("rads_mock_api", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return TestClient(mod.create_app(token=TOKEN))


HB = {"camera_id": 1, "status": "online", "fps": 4.9, "ts": "2026-12-02T14:05:31+05:00"}


@pytest.mark.parametrize("path,body", [(PATH_INCIDENT_NEW, {"incident_id": 1}),
                                       (PATH_INCIDENT_CLOSED, {"incident_id": 1}), (PATH_HEARTBEAT, HB)])
def test_bad_or_missing_token_is_401(client, path, body):
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, json=body, headers={INTERNAL_HEADER: "wrong"}).status_code == 401
    assert client.post(path, json={"junk": 1}).status_code == 401      # token is checked before the body
    assert client.get("/debug/calls").json() == []


def test_good_calls_are_recorded(client, capsys):
    h = {INTERNAL_HEADER: TOKEN}
    assert client.post(PATH_INCIDENT_NEW, json={"incident_id": 1042}, headers=h).json() == {"ok": True}
    assert client.post(PATH_INCIDENT_CLOSED, json={"incident_id": 1042}, headers=h).status_code == 200
    assert client.post(PATH_HEARTBEAT, json=HB, headers=h).status_code == 200
    calls = client.get("/debug/calls").json()
    assert [c["path"] for c in calls] == [PATH_INCIDENT_NEW, PATH_INCIDENT_CLOSED, PATH_HEARTBEAT]
    assert calls[0]["body"] == {"incident_id": 1042} and calls[2]["body"] == HB
    assert "POST /internal/incidents/new" in capsys.readouterr().out


def test_bodies_are_validated_with_the_contract(client):
    h = {INTERNAL_HEADER: TOKEN}
    assert client.post(PATH_INCIDENT_NEW, json={"incident_id": "abc"}, headers=h).status_code == 422
    assert client.post(PATH_INCIDENT_NEW, json={"incident_id": 1, "extra": 2}, headers=h).status_code == 422
    assert client.post(PATH_HEARTBEAT, json={**HB, "status": "sleeping"}, headers=h).status_code == 422
    assert client.post(PATH_HEARTBEAT, json={**HB, "ts": "2026-12-02T14:05:31"}, headers=h).status_code == 422
    assert client.get("/debug/calls").json() == []


def test_keeps_only_the_last_100(client):
    h = {INTERNAL_HEADER: TOKEN}
    for i in range(130):
        client.post(PATH_INCIDENT_NEW, json={"incident_id": i}, headers=h)
    calls = client.get("/debug/calls").json()
    assert len(calls) == 100 and calls[0]["body"]["incident_id"] == 30
