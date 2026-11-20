#!/usr/bin/env python3
"""Phase 2 step 4: ultralytics dataset check + 1-epoch smoke test (YOLO26s, imgsz 640, batch 4, workers 0).
Runs inside <prepared_dir>/_work/smoke so downloaded weights and run folders stay out of dataset/.
Removes the ultralytics *.cache files from dataset/labels afterwards (they are machine-specific).

Usage: python 07_smoke_test.py <prepared_dir>
"""
import sys, os, json, time, traceback
from pathlib import Path

OUT = Path(sys.argv[1]).resolve()
DATA = OUT / "dataset" / "data.yaml"
SM = OUT / "_work" / "smoke"; SM.mkdir(parents=True, exist_ok=True)
os.chdir(SM)
res = {"data_yaml": str(DATA)}
import ultralytics
from ultralytics import YOLO
from ultralytics.data.utils import check_det_dataset
res["ultralytics_version"] = ultralytics.__version__
try:
    info = check_det_dataset(str(DATA))
    res["check_det_dataset"] = "OK"
    res["check_det_dataset_nc"] = info.get("nc")
    res["check_det_dataset_names"] = info.get("names")
except Exception as e:
    res["check_det_dataset"] = f"ERROR: {e!r}"; traceback.print_exc()
t0 = time.time()
try:
    model = YOLO("yolo26s.pt")
    r = model.train(data=str(DATA), epochs=1, imgsz=640, batch=4, workers=0, project=str(SM), name="run", exist_ok=True)
    res["smoke_test"] = "OK"
    res["device"] = str(model.trainer.device) if getattr(model, "trainer", None) else None
    try:
        res["metrics"] = {k: round(float(v), 4) for k, v in r.results_dict.items()}
    except Exception:
        pass
except Exception as e:
    res["smoke_test"] = f"ERROR: {e!r}"; traceback.print_exc()
res["smoke_minutes"] = round((time.time() - t0) / 60, 1)
for c in (OUT / "dataset" / "labels").glob("*.cache"):
    c.unlink()
json.dump(res, open(OUT / "_work" / "smoke_result.json", "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
