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
                    color: tuple[int, int, int] | None = None, thickness: int = 2, prefix: str = "",
                    k: float = 1.0) -> np.ndarray:
    """Draw boxes with `name conf` labels in place; `scale` maps detection coords to `img` coords; `k` scales
    text and lines (1 = preview, ~2.5 = full-resolution evidence)."""
    fs, tt = 0.45 * k, max(1, round(k))
    for d in dets:
        x1, y1, x2, y2 = (int(round(v * scale)) for v in d["xyxy"])
        c = color or color_for(d["cls_name"])
        cv2.rectangle(img, (x1, y1), (x2, y2), c, max(1, round(thickness * k)))
        label = f'{prefix}{d["cls_name"]} {d["conf"]:.2f}'
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, tt)
        ty = max(y1, th + base + 2)
        cv2.rectangle(img, (x1, ty - th - base - 2), (x1 + tw + 4, ty), c, -1)
        cv2.putText(img, label, (x1 + 2, ty - base - 1), cv2.FONT_HERSHEY_SIMPLEX, fs, WHITE, tt, cv2.LINE_AA)
    return img


def draw_overlay(img: np.ndarray, lines: list[str], origin: tuple[int, int] = (8, 8), k: float = 1.0) -> np.ndarray:
    """Text lines on a dark box in the top-left corner, in place."""
    if not lines:
        return img
    font, fs, th = cv2.FONT_HERSHEY_SIMPLEX, 0.5 * k, max(1, round(k))
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


def _label(img, text, x, y, bg, fs=0.5, tt=1):
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, tt)
    y = max(y, th + base + 2)
    cv2.rectangle(img, (x, y - th - base - 4), (x + tw + 6, y), bg, -1)
    cv2.putText(img, text, (x + 3, y - base - 2), cv2.FONT_HERSHEY_SIMPLEX, fs, WHITE, tt, cv2.LINE_AA)


def draw_zones(img: np.ndarray, zones, scale: float = 1.0, alpha: float = 0.18, k: float = 1.0) -> np.ndarray:
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
        cv2.polylines(img, [pts], True, color, max(1, round(2 * k)), cv2.LINE_AA)
        x, y = pts.min(axis=0)
        _label(img, f"{z.name} ({z.type})", int(x) + 4, int(y) + int(22 * k), color, fs=0.45 * k, tt=max(1, round(k)))
    return img


def draw_workers(img: np.ndarray, workers, scale: float = 1.0, k: float = 1.0) -> np.ndarray:
    """Track box, `ID n H:.. M:.. A:..` label (red if any violation) and a feet dot (filled = in a zone)."""
    for w in workers:
        x1, y1, x2, y2 = (int(round(v * scale)) for v in w.box)
        color = track_color(w.track_id)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, max(1, round(3 * k)))
        text = f"ID {w.track_id} H:{SHORT[w.head]} M:{SHORT[w.mask]} A:{SHORT[w.apron]}"
        _label(img, text, x1, y1, RED if w.violations() else color, fs=0.5 * k, tt=max(1, round(k)))
        feet = ((x1 + x2) // 2, y2)
        cv2.circle(img, feet, int(6 * k), color, -1 if w.zone_ids else max(1, round(2 * k)), cv2.LINE_AA)
    return img


def draw_evidence(frame: np.ndarray, zones, dets: list[dict[str, Any]], workers, trigger: dict[str, Any] | None,
                  subject_box, title_lines: list[str], k: float = 2.5) -> np.ndarray:
    """Evidence snapshot at full resolution, in place: the rule's zone(s), all detections and workers, and the
    incident's subject (worker box) and triggering detection in thick red, with a title block."""
    draw_zones(frame, zones, 1.0, k=k)
    draw_detections(frame, [d for d in dets if d["cls_name"] != "person"], 1.0, thickness=1, k=k)
    draw_workers(frame, workers, 1.0, k=k)
    th = max(2, round(4 * k))
    if subject_box is not None:
        x1, y1, x2, y2 = (int(round(v)) for v in subject_box)
        cv2.rectangle(frame, (x1, y1), (x2, y2), RED, th)
    if trigger and trigger.get("xyxy"):
        x1, y1, x2, y2 = (int(round(v)) for v in trigger["xyxy"])
        pad = int(6 * k)
        cv2.rectangle(frame, (x1 - pad, y1 - pad), (x2 + pad, y2 + pad), RED, th)
        _label(frame, f'{trigger["cls_name"]} {trigger["conf"]:.2f}', x1 - pad, y2 + pad + int(26 * k), RED,
               fs=0.5 * k, tt=max(1, round(k)))
    draw_overlay(frame, title_lines, origin=(int(8 * k), int(8 * k)), k=k)
    return frame


def annotate(image: np.ndarray, result, zones, width: int, lines: list[str]) -> np.ndarray:
    """Live-view image (preview window and /stream): a resized COPY of `image` with zones, kept detections, ignored
    items (dark grey), untracked persons (grey), workers with IDs, H/M/A states and feet dots, and an overlay.
    `result` is a FrameResult (duck-typed); the shared frame itself is never drawn on."""
    img, scale = resize_for_display(image, width)
    draw_zones(img, zones, scale)
    draw_detections(img, result.ignored_persons + result.dropped_parts + result.removed, scale,
                    color=DARK_GREY, thickness=1, prefix="ign ")
    draw_detections(img, [d for d in result.dets if d["cls_name"] != "person"], scale)
    draw_detections(img, result.untracked, scale, color=GREY, thickness=1)
    draw_workers(img, result.workers, scale)
    draw_overlay(img, lines)
    return img
