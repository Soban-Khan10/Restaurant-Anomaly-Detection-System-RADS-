"""Drawing helpers: detection boxes and a status overlay. Phase 2 adds track IDs and zones."""
from __future__ import annotations

from typing import Any, Iterable

import cv2
import numpy as np

from shared.config import load_defaults

# BGR
RED = (0, 0, 255)
GREEN = (0, 200, 0)
BLUE = (255, 128, 0)
YELLOW = (0, 215, 255)
WHITE = (255, 255, 255)


def _class_sets() -> tuple[set[str], set[str]]:
    """Violation / compliant class names from the hazard table in config/defaults.yaml."""
    try:
        hazards = load_defaults().get("hazards", []) or []
    except Exception:
        hazards = []
    viol = {h["violation_class"] for h in hazards if h.get("violation_class")}
    comp = {h["compliant_class"] for h in hazards if h.get("compliant_class")}
    return viol, comp


VIOLATION_CLASSES, COMPLIANT_CLASSES = _class_sets()


def color_for(cls_name: str) -> tuple[int, int, int]:
    if cls_name in VIOLATION_CLASSES:
        return RED
    if cls_name in COMPLIANT_CLASSES:
        return GREEN
    if cls_name == "person":
        return BLUE
    return YELLOW


def resize_for_display(frame: np.ndarray, width: int) -> tuple[np.ndarray, float]:
    """A resized copy for display and the scale from frame pixels to display pixels."""
    h, w = frame.shape[:2]
    if width <= 0 or width >= w:
        return frame.copy(), 1.0
    scale = width / w
    return cv2.resize(frame, (width, int(round(h * scale))), interpolation=cv2.INTER_LINEAR), scale


def draw_detections(img: np.ndarray, dets: Iterable[dict[str, Any]], scale: float = 1.0,
                    color: tuple[int, int, int] | None = None, thickness: int = 2, prefix: str = "") -> np.ndarray:
    """Draw boxes with `name conf` labels in place; `scale` maps detection coords to `img` coords."""
    for d in dets:
        x1, y1, x2, y2 = (int(round(v * scale)) for v in d["xyxy"])
        c = color or color_for(d["cls_name"])
        cv2.rectangle(img, (x1, y1), (x2, y2), c, thickness)
        label = f'{prefix}{d["cls_name"]} {d["conf"]:.2f}'
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        ty = max(y1, th + base + 2)
        cv2.rectangle(img, (x1, ty - th - base - 2), (x1 + tw + 4, ty), c, -1)
        cv2.putText(img, label, (x1 + 2, ty - base - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.45, WHITE, 1, cv2.LINE_AA)
    return img


def draw_overlay(img: np.ndarray, lines: list[str], origin: tuple[int, int] = (8, 8)) -> np.ndarray:
    """Text lines on a dark box in the top-left corner, in place."""
    if not lines:
        return img
    font, fs, th = cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
    sizes = [cv2.getTextSize(s, font, fs, th)[0] for s in lines]
    lh = max(h for _, h in sizes) + 8
    w = max(w for w, _ in sizes) + 12
    x, y = origin
    sub = img[y:y + lh * len(lines) + 4, x:x + w]
    sub[:] = (sub * 0.35).astype(img.dtype)
    for i, s in enumerate(lines):
        cv2.putText(img, s, (x + 6, y + (i + 1) * lh - 4), font, fs, WHITE, th, cv2.LINE_AA)
    return img


# --------------------------------------------------------------------------- Phase 2: zones, tracks, linked parts

GREY = (160, 160, 160)
DARK_GREY = (70, 70, 70)
ORANGE = (0, 140, 255)
ZONE_COLORS = {"prep": (80, 200, 80), "waste": ORANGE, "ignore": GREY}
SHORT = {"covered": "cov", "uncovered": "UNCOV", "on": "on", "off": "OFF", "yes": "yes", "no": "NO", None: "-"}


def track_color(track_id: int) -> tuple[int, int, int]:
    """A stable, fairly bright BGR colour per track ID."""
    h = (track_id * 47) % 180
    hsv = np.uint8([[[h, 200, 255]]])
    b, g, r = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    return int(b), int(g), int(r)


def _label(img, text, x, y, bg, fs=0.5):
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)
    y = max(y, th + base + 2)
    cv2.rectangle(img, (x, y - th - base - 4), (x + tw + 6, y), bg, -1)
    cv2.putText(img, text, (x + 3, y - base - 2), cv2.FONT_HERSHEY_SIMPLEX, fs, WHITE, 1, cv2.LINE_AA)


def draw_zones(img: np.ndarray, zones, scale: float = 1.0, alpha: float = 0.18) -> np.ndarray:
    """Translucent fill + outline + `name (type)` for each zone (prep green, waste orange, ignore grey), in place."""
    if not zones:
        return img
    overlay = img.copy()
    polys = []
    for z in zones:
        pts = np.array([[int(round(x * scale)), int(round(y * scale))] for x, y in z.polygon], dtype=np.int32)
        color = ZONE_COLORS.get(str(z.type), YELLOW)
        cv2.fillPoly(overlay, [pts], color)
        polys.append((z, pts, color))
    cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0, dst=img)
    for z, pts, color in polys:
        cv2.polylines(img, [pts], True, color, 2, cv2.LINE_AA)
        x, y = pts.min(axis=0)
        _label(img, f"{z.name} ({z.type})", int(x) + 4, int(y) + 22, color, fs=0.45)
    return img


def draw_workers(img: np.ndarray, workers, scale: float = 1.0) -> np.ndarray:
    """Track box, `ID n H:.. M:.. A:..` label (red if any violation) and a feet dot (filled = in a zone)."""
    for w in workers:
        x1, y1, x2, y2 = (int(round(v * scale)) for v in w.box)
        color = track_color(w.track_id)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 3)
        text = f"ID {w.track_id} H:{SHORT[w.head]} M:{SHORT[w.mask]} A:{SHORT[w.apron]}"
        _label(img, text, x1, y1, RED if w.violations() else color)
        feet = ((x1 + x2) // 2, y2)
        cv2.circle(img, feet, 6, color, -1 if w.zone_ids else 2, cv2.LINE_AA)
    return img
