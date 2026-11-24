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


def draw_detections(img: np.ndarray, dets: Iterable[dict[str, Any]], scale: float = 1.0) -> np.ndarray:
    """Draw boxes with `name conf` labels in place; `scale` maps detection coords to `img` coords."""
    for d in dets:
        x1, y1, x2, y2 = (int(round(v * scale)) for v in d["xyxy"])
        color = color_for(d["cls_name"])
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        label = f'{d["cls_name"]} {d["conf"]:.2f}'
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        ty = max(y1, th + base + 2)
        cv2.rectangle(img, (x1, ty - th - base - 2), (x1 + tw + 4, ty), color, -1)
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
