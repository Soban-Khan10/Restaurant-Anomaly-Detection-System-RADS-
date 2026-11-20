#!/usr/bin/env python3
"""Clip-based train/val split search on the CLEANED data (clean_lib.load_clean).

Hard rules (user, 2026-11-20):
  - a source video clip is never split (group = clip; recording date is taken from the clip name)
  - val = 12-18% of frames
  - train keeps >= 75% of EVERY class (val share <= 25%)
  - at least one camera-view-B (camera 2) clip in val
Objective, lexicographic:
  1. number of classes with >= 10 val boxes ("at least 10 per class where possible")
  2. sum over classes of min(val boxes, 20)
  3. the smallest per-class val share (more balanced)
  4. val frame share closest to 15%
Exhaustive over all 2^n clip subsets.  Output: audit/split/ (proposal only, nothing applied).

Usage: python 04_split_search.py <prepared_dir>
"""
import sys, json
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
from clean_lib import load_clean, CLASSES

OUT = Path(sys.argv[1]).resolve()
SD = OUT / "audit" / "split"; SD.mkdir(parents=True, exist_ok=True)
F, B, log, *_ = load_clean(OUT)

clips = F.groupby("clip").agg(frames=("frame_id", "count"), camera=("camera", lambda x: ",".join(sorted(set(x)))),
                              camera_view=("camera_view", lambda x: ",".join(sorted(set(x)))),
                              recording_date=("recording_date", "first"), recording_start=("recording_start", "first"))
cb = B.pivot_table(index="clip", columns="class_name", values="cx", aggfunc="count", fill_value=0) \
      .reindex(index=clips.index, columns=CLASSES, fill_value=0).fillna(0).astype(np.int64)
clips = clips.join(cb)
names = clips.index.tolist(); n = len(names)
fr = clips.frames.values.astype(np.int64); C = cb.values; totC = C.sum(0); totF = fr.sum()
isB = clips.camera_view.str.contains("B").values.astype(np.int64)

best, n_valid = None, 0
masks = np.arange(1, 2 ** n - 1, dtype=np.int64)
for s in range(0, len(masks), 1 << 16):
    m = masks[s:s + (1 << 16)]
    sel = ((m[:, None] >> np.arange(n)) & 1).astype(np.int64)
    vf = (sel * fr).sum(1)
    vb = np.stack([(sel * C[:, k]).sum(1) for k in range(len(CLASSES))], 1)
    share = vb / np.maximum(totC, 1)
    ok = (vf >= 0.12 * totF) & (vf <= 0.18 * totF) & (share <= 0.25).all(1) & ((sel * isB).sum(1) >= 1)
    n_valid += int(ok.sum())
    if not ok.any():
        continue
    key = np.stack([(vb >= 10).sum(1), np.minimum(vb, 20).sum(1), np.round(share.min(1), 6),
                    -np.abs(vf / totF - 0.15)], 1)[ok]
    mm = m[ok]
    order = np.lexsort(key[:, ::-1].T)  # last key column is primary in lexsort -> reverse
    k = order[-1]
    cand = (tuple(key[k]), int(mm[k]))
    if best is None or cand[0] > best[0]:
        best = cand

assert best is not None, "no subset satisfies the hard rules"
val = [names[i] for i in range(n) if (best[1] >> i) & 1]
F["split"] = np.where(F["clip"].isin(val), "val", "train")
B = B.merge(F[["frame_id", "split"]], on="frame_id")

rows = []
for c in CLASSES:
    t = int(((B.class_name == c) & (B.split == "train")).sum()); v = int(((B.class_name == c) & (B.split == "val")).sum())
    rows.append({"class_id": CLASSES.index(c), "class": c, "train": t, "val": v, "total": t + v,
                 "train_pct": round(100 * t / (t + v), 1) if t + v else None,
                 "val_pct": round(100 * v / (t + v), 1) if t + v else None,
                 "val_ge_10": v >= 10, "train_ge_75pct": t >= 0.75 * (t + v)})
T = pd.DataFrame(rows)
tot = {"train_frames": int((F.split == "train").sum()), "val_frames": int((F.split == "val").sum()),
       "train_boxes": int((B.split == "train").sum()), "val_boxes": int((B.split == "val").sum()),
       "train_background": int(((F.split == "train") & (F.num_boxes_clean == 0)).sum()),
       "val_background": int(((F.split == "val") & (F.num_boxes_clean == 0)).sum()),
       "val_clips": val, "valid_subsets": n_valid, "subsets_searched": int(2 ** n - 2),
       "classes_val_ge_10": int(T.val_ge_10.sum())}
T.to_csv(SD / "split_per_class.csv", index=False)
clips.assign(split=np.where(clips.index.isin(val), "val", "train")).to_csv(SD / "split_per_clip.csv")
F[["frame_id", "clip", "recording_date", "camera", "split"]].to_csv(SD / "split_per_frame.csv", index=False)
json.dump(tot, open(SD / "split_summary.json", "w"), indent=1)
log.to_csv(SD / "cleaning_log.csv", index=False)
print(json.dumps(tot, indent=1)); print(T.to_string(index=False))
print(clips.assign(split=np.where(clips.index.isin(val), "val", "train"))[["frames", "camera", "recording_date", "split"]].to_string())
