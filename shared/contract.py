"""RADS shared contract: enums, constants, ports and the inter-process message models (contract section 5).

Changed only by agreement of both members.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------- time

TZ = ZoneInfo("Asia/Karachi")  # +05:00; on Windows this needs the `tzdata` package


def now_iso() -> str:
    """Current time as ISO 8601 text with offset, e.g. 2026-12-02T14:05:31+05:00."""
    return datetime.now(TZ).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- ports / internal calls

API_HOST = "127.0.0.1"
API_PORT = 8000
WORKER_HOST = "127.0.0.1"
WORKER_PORT = 8001

INTERNAL_HEADER = "X-RADS-Internal"  # value: RADS_INTERNAL_TOKEN; wrong or missing -> 401

# worker -> app
PATH_INCIDENT_NEW = "/internal/incidents/new"        # message 2
PATH_INCIDENT_CLOSED = "/internal/incidents/closed"  # message 5
PATH_HEARTBEAT = "/internal/heartbeat"               # message 6
# app -> worker
PATH_WORKER_RELOAD = "/reload"                       # message 7
PATH_WORKER_STREAM = "/stream/{camera_id}"           # message 8 (MJPEG)
# app relay of message 8, and the dashboard WebSocket (message 3)
PATH_API_CAMERA_STREAM = "/api/cameras/{camera_id}/stream"
PATH_WS = "/ws"

HEARTBEAT_INTERVAL_S = 5      # worker sends one per camera every 5 s
CAMERA_OFFLINE_AFTER_S = 15   # app shows a camera offline after 15 s without heartbeat
NOTIFY_RETRIES = 3            # worker retries messages 2/5 up to 3 times, then gives up
STREAM_FPS = 2                # MJPEG live view ~2 FPS

# --------------------------------------------------------------------------- enums (match schema.sql CHECKs)


class RoleName(StrEnum):
    ADMIN = "Admin"
    MANAGER = "Manager"
    TECHNICAL = "Technical"


class CameraStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    RECONNECTING = "reconnecting"


class ZoneType(StrEnum):
    PREP = "prep"
    WASTE = "waste"
    IGNORE = "ignore"


class TiedTo(StrEnum):
    WORKER = "worker"
    ZONE = "zone"


class Severity(StrEnum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class IncidentStatus(StrEnum):
    NEW = "New"
    ACKNOWLEDGED = "Acknowledged"
    CONFIRMED = "Confirmed violation"
    FALSE_ALARM = "False alarm"


class EvidenceKind(StrEnum):
    SNAPSHOT = "snapshot"
    CLIP = "clip"


class AlertChannel(StrEnum):
    DASHBOARD = "dashboard"
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"


class AlertKind(StrEnum):
    FIRST = "first"
    ESCALATION = "escalation"


class AlertStatus(StrEnum):
    SENT = "sent"
    FAILED = "failed"


class ReportFormat(StrEnum):
    PDF = "pdf"
    XLSX = "xlsx"


class HazardId(StrEnum):
    H01 = "H-01"  # Head cover missing
    H04 = "H-04"  # Phone in preparation area
    H05 = "H-05"  # Bin left open
    H06 = "H-06"  # Apron missing
    H08 = "H-08"  # Mask missing


# The 10 classes the rules use. Map to ids via model.names at runtime, never hard-code ids.
RULE_CLASSES: tuple[str, ...] = (
    "person", "head_covered", "head_uncovered", "mask_on", "mask_off",
    "apron", "no_apron", "phone", "bin_closed", "bin_open",
)

# --------------------------------------------------------------------------- message models (section 5)


class _Msg(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_iso(v: str) -> str:
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset, e.g. +05:00")
    return v


class IncidentRef(_Msg):
    """Messages 2 and 5: POST /internal/incidents/new and /internal/incidents/closed."""
    incident_id: int


IncidentNew = IncidentRef
IncidentClosed = IncidentRef


class Heartbeat(_Msg):
    """Message 6: POST /internal/heartbeat (worker -> app, every 5 s per camera)."""
    camera_id: int
    status: CameraStatus
    fps: float | None = None
    ts: str

    _ts = field_validator("ts")(_check_iso)


class ReloadRequest(_Msg):
    """Message 7: POST {WORKER}/reload, body {}."""


class ReloadResponse(_Msg):
    """Message 7 answer: {"ok": true}."""
    ok: bool = True


# Message 3: WebSocket app -> dashboards


class WsIncidentNew(_Msg):
    type: Literal["incident.new"] = "incident.new"
    incident: dict[str, Any] = Field(description="the full incidents row")


class WsIncidentClosed(_Msg):
    type: Literal["incident.closed"] = "incident.closed"
    incident_id: int


class WsCameraStatus(Heartbeat):
    """{"type": "camera.status", ...heartbeat fields...}"""
    type: Literal["camera.status"] = "camera.status"


WsMessage = WsIncidentNew | WsIncidentClosed | WsCameraStatus
