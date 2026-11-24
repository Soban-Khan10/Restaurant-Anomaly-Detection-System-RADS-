"""Detector: loads the YOLO model once and returns plain detections.

The backend is a single setting: RADS_MODEL_PATH may be a .pt (PyTorch, FP32) or an exported .mlpackage; both load
the same way. Class names always come from `model.names`.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from shared.contract import RULE_CLASSES

log = logging.getLogger(__name__)

GLOBAL_CONF = 0.25  # low global threshold; per-rule thresholds are applied later


def resolve_device(setting: str | None) -> str:
    """RADS_DEVICE -> torch device string. `auto` = cuda if available, else mps, else cpu."""
    s = (setting or "auto").strip().lower()
    if s != "auto":
        return s
    import torch

    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


class Detector:
    def __init__(self, model_path: str | Path, imgsz: int = 960, device: str = "auto",
                 conf: float = GLOBAL_CONF) -> None:
        from ultralytics import YOLO

        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"model not found: {path}")
        self.path = path
        self.imgsz = imgsz
        self.conf = conf
        self.device = resolve_device(device)
        self.model_version = path.stem
        self.model = YOLO(str(path), task="detect")
        self.names: dict[int, str] = {int(k): str(v) for k, v in self.model.names.items()}
        missing = [c for c in RULE_CLASSES if c not in self.names.values()]
        if missing:
            log.warning("model is missing rule classes: %s", ", ".join(missing))
        log.info("model %s loaded: device=%s (setting %r) imgsz=%d conf=%.2f classes=%d",
                 path.name, self.device, device, imgsz, conf, len(self.names))

    def detect(self, frame: np.ndarray) -> list[dict[str, Any]]:
        """Detections above the global threshold: [{cls_name, conf, xyxy}] in frame pixels."""
        r = self.model.predict(frame, imgsz=self.imgsz, conf=self.conf, device=self.device,
                               verbose=False)[0]  # FP32 (ultralytics default)
        boxes = r.boxes
        if boxes is None or len(boxes) == 0:
            return []
        xyxy = boxes.xyxy.cpu().numpy()   # .cpu() also waits for the GPU, so timings are honest
        conf = boxes.conf.cpu().numpy()
        cls = boxes.cls.cpu().numpy().astype(int)
        return [
            {"cls_name": self.names[int(c)], "conf": float(p), "xyxy": tuple(float(v) for v in b)}
            for b, p, c in zip(xyxy, conf, cls)
        ]

    def warmup(self, frame: np.ndarray | None = None, n: int = 10) -> None:
        if frame is None:
            frame = np.full((1520, 2688, 3), 114, dtype=np.uint8)
        for _ in range(n):
            self.detect(frame)
        log.info("detector warmed up with %d frames", n)
