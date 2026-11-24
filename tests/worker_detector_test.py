from pathlib import Path

import cv2
import pytest
import torch

from shared.config import get_settings
from shared.contract import RULE_CLASSES
from worker.detector import GLOBAL_CONF, Detector, resolve_device

CLIP = Path.home() / "Desktop/RADS_Edge_M4/clips/20261109230051844.mp4"


@pytest.mark.parametrize("setting", ["cpu", "mps", "cuda", " CPU "])
def test_explicit_device_is_kept(setting):
    assert resolve_device(setting) == setting.strip().lower()


@pytest.mark.parametrize("cuda,mps,expected", [(True, True, "cuda"), (False, True, "mps"), (False, False, "cpu")])
def test_auto_device_order(monkeypatch, cuda, mps, expected):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda)
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: mps)
    assert resolve_device("auto") == expected
    assert resolve_device(None) == expected
    assert resolve_device("") == expected


def test_missing_model_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Detector(tmp_path / "nope.pt")


@pytest.mark.skipif(not get_settings().model_path.exists() or not CLIP.exists(), reason="model or clip not present")
def test_real_model_on_clip_frame():
    s = get_settings()
    det = Detector(s.model_path, s.imgsz, s.device)
    assert set(RULE_CLASSES) <= set(det.names.values())
    assert det.model_version == s.model_path.stem

    cap = cv2.VideoCapture(str(CLIP), cv2.CAP_FFMPEG)
    for _ in range(50):  # a frame a couple of seconds in
        ok, frame = cap.read()
    cap.release()
    assert ok

    det.warmup(frame, n=2)
    dets = det.detect(frame)
    assert isinstance(dets, list) and dets, "expected some detections on a kitchen frame"
    h, w = frame.shape[:2]
    for d in dets:
        assert set(d) == {"cls_name", "conf", "xyxy"}
        assert d["cls_name"] in det.names.values()
        assert isinstance(d["conf"], float) and GLOBAL_CONF <= d["conf"] <= 1.0
        x1, y1, x2, y2 = d["xyxy"]
        assert 0 <= x1 < x2 <= w + 1 and 0 <= y1 < y2 <= h + 1
