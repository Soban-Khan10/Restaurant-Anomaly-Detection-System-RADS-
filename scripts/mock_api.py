"""Stand-in for the RADS app server, for testing the worker alone (Member A, contract section 5).

Listens on 127.0.0.1:8000 and implements the three worker -> app calls:
    POST /internal/incidents/new      {"incident_id": 1042}                      (message 2)
    POST /internal/incidents/closed   {"incident_id": 1042}                      (message 5)
    POST /internal/heartbeat          {"camera_id", "status", "fps", "ts"}       (message 6)
Every call needs the header X-RADS-Internal: <RADS_INTERNAL_TOKEN>; wrong or missing -> 401. Bodies are validated
with the shared contract models (invalid -> 422). Each call is printed and the last 100 are kept in memory:
    GET /debug/calls

    .venv/bin/python scripts/mock_api.py
"""
from __future__ import annotations

import sys
from collections import deque
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # allow `python scripts/mock_api.py`

from fastapi import Depends, FastAPI, Header, HTTPException  # noqa: E402

from shared.config import get_settings  # noqa: E402
from shared.contract import (API_HOST, API_PORT, INTERNAL_HEADER, PATH_HEARTBEAT, PATH_INCIDENT_CLOSED,  # noqa: E402
                             PATH_INCIDENT_NEW, TZ, Heartbeat, IncidentClosed, IncidentNew)

MAX_CALLS = 100


def create_app(token: str | None = None) -> FastAPI:
    token = get_settings().internal_token if token is None else token
    calls: deque[dict] = deque(maxlen=MAX_CALLS)
    app = FastAPI(title="RADS mock API")
    app.state.calls = calls

    def check_token(x_rads_internal: str | None = Header(default=None, alias=INTERNAL_HEADER)) -> None:
        if not token or x_rads_internal != token:
            raise HTTPException(status_code=401, detail="bad or missing internal token")

    def record(path: str, body) -> dict:
        entry = {"received_at": datetime.now(TZ).isoformat(timespec="milliseconds"), "path": path,
                 "body": body.model_dump(mode="json")}
        calls.append(entry)
        print(f"{entry['received_at']} POST {path} {entry['body']}", flush=True)
        return {"ok": True}

    @app.post(PATH_INCIDENT_NEW, dependencies=[Depends(check_token)])
    def incident_new(body: IncidentNew) -> dict:
        return record(PATH_INCIDENT_NEW, body)

    @app.post(PATH_INCIDENT_CLOSED, dependencies=[Depends(check_token)])
    def incident_closed(body: IncidentClosed) -> dict:
        return record(PATH_INCIDENT_CLOSED, body)

    @app.post(PATH_HEARTBEAT, dependencies=[Depends(check_token)])
    def heartbeat(body: Heartbeat) -> dict:
        return record(PATH_HEARTBEAT, body)

    @app.get("/debug/calls")
    def debug_calls() -> list[dict]:
        return list(calls)

    return app


def main() -> None:
    import uvicorn

    if not get_settings().internal_token:
        sys.exit("RADS_INTERNAL_TOKEN is not set in .env")
    print(f"RADS mock API on http://{API_HOST}:{API_PORT}", flush=True)
    uvicorn.run(create_app(), host=API_HOST, port=API_PORT, log_level="warning")


if __name__ == "__main__":
    main()
