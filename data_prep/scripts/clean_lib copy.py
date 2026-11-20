"""Shared cleaning used by the split search (04) and the dataset build (05).
Applies the user's Phase 1 decisions (2026-11-20) on top of the frame_id de-duplication done in 03_audit.py:
  - rename phone_in_hand -> phone, mask_incorrect -> mask_off (by name)
  - drop tiny boxes (< 4 px wide or high in the original image)
  - remove exact duplicate boxes (same class, IoU > 0.9; first one kept)
  - same image saved under two frame_ids: keep only the latest-edited copy
  - clip boxes to the image
  - restaurant R01cutte-t -> R01; camera view A = camera 1, B = camera 2
  - group = source video clip; recording date from the clip file name (YYYYMMDD...)
"""
import itertools
from pathlib import Path
import numpy as np
import pandas as pd

CLASSES = ["person", "head_covered", "head_uncovered", "mask_on", "mask_off", "apron", "no_apron",
           "hand_bare", "hand_gloved", "rte_food", "raw_meat", "smoking", "phone", "bin_closed", "bin_open"]
CLASS_ID = {c: i for i, c in enumerate(CLASSES)}
RENAME = {"phone_in_hand": "phone", "mask_incorrect": "mask_off"}
RESTAURANT_FIX = {"R01cutte-t": "R01"}
CAMERA = {"A": "camera 1", "B": "camera 2"}


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def load_clean(out_dir):
    W = Path(out_dir) / "_work"
    F = pd.read_csv(W / "frames_kept.csv", dtype={"clip_ts": str})
    B = pd.read_csv(W / "boxes_kept.csv", dtype={"clip_ts": str})
    log = []  # (step, count, detail)
    B = B[["row_uid", "frame_id", "export", "box_idx", "class_name", "src_class_id", "cx", "cy", "bw", "bh",
           "meta_width", "meta_height", "status", "hazard"]].copy()
    B["source_class_name"] = B["class_name"]

    # 1. same image under two frame_ids -> keep latest updated_at
    F["_upd"] = pd.to_datetime(F["updated_at"], utc=True, errors="coerce")
    drop_fids, cd_rows = set(), []
    groups = [g for _, g in F.groupby(["clip_ts", "frame_index"]) if len(g) > 1]
    groups += [g for _, g in F.groupby("sha1") if len(g) > 1]
    seen = set()
    for g in groups:
        key = tuple(sorted(g.frame_id))
        if key in seen:
            continue
        seen.add(key)
        g = g.sort_values("_upd")
        keep = g.iloc[-1]
        for _, r in g.iloc[:-1].iterrows():
            drop_fids.add(r.frame_id)
            cd_rows.append({"dropped_frame_id": r.frame_id, "dropped_updated_at": r.updated_at,
                            "dropped_boxes": r.num_boxes, "kept_frame_id": keep.frame_id,
                            "kept_updated_at": keep.updated_at, "kept_boxes": keep.num_boxes})
    n_box_drop = int(B.frame_id.isin(drop_fids).sum())
    F = F[~F.frame_id.isin(drop_fids)].copy()
    B = B[~B.frame_id.isin(drop_fids)].copy()
    log.append(("same image under two frame_ids: older copy removed", len(drop_fids),
                f"{n_box_drop} boxes on the removed copies"))

    # 2. rename other classes by name
    ren = []
    for src, dst in RENAME.items():
        m = B.class_name == src
        ren.append((src, dst, int(m.sum()), sorted(B[m].frame_id.unique())))
        B.loc[m, "class_name"] = dst
    for src, dst, n, f in ren:
        log.append((f"renamed {src} -> {dst}", n, f"{len(f)} frames"))
    unknown = B[~B.class_name.isin(CLASSES)]
    assert unknown.empty, f"unexpected classes: {unknown.class_name.unique()}"

    # 3. tiny boxes
    w_px, h_px = B.bw * B.meta_width, B.bh * B.meta_height
    tiny = B[(w_px < 4) | (h_px < 4)]
    tiny_rows = tiny[["frame_id", "class_name"]].assign(w_px=w_px[tiny.index].round(2), h_px=h_px[tiny.index].round(2))
    B = B.drop(tiny.index)
    log.append(("tiny boxes dropped (< 4 px)", len(tiny), ", ".join(f"{r.class_name} in {r.frame_id}" for r in tiny_rows.itertuples())))

    # 4. clip boxes to image (before duplicate test so IoU is on final geometry)
    x1 = (B.cx - B.bw / 2).clip(0, 1); y1 = (B.cy - B.bh / 2).clip(0, 1)
    x2 = (B.cx + B.bw / 2).clip(0, 1); y2 = (B.cy + B.bh / 2).clip(0, 1)
    changed = ((x2 - x1 - B.bw).abs() > 1e-9) | ((y2 - y1 - B.bh).abs() > 1e-9)
    over = np.maximum.reduce([-(B.cx - B.bw / 2), -(B.cy - B.bh / 2), B.cx + B.bw / 2 - 1, B.cy + B.bh / 2 - 1])
    log.append(("boxes clipped to the image", int(changed.sum()),
                f"rounding only: largest overshoot {over.max() * 2688:.4f} px; no box was really outside the image"
                if over.max() < 1e-6 else f"largest overshoot {over.max():.6f} of image size"))
    B["cx"], B["cy"], B["bw"], B["bh"] = (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1

    # 5. exact duplicate boxes (same class after renaming, IoU > 0.9) - keep the first
    dup_idx, dup_rows = [], []
    for fid, g in B.groupby("frame_id"):
        xy = list(zip(g.index, g.class_name, g.source_class_name, g.cx - g.bw / 2, g.cy - g.bh / 2, g.cx + g.bw / 2, g.cy + g.bh / 2))
        removed = set()
        for a, b in itertools.combinations(xy, 2):
            if a[0] in removed or b[0] in removed or a[1] != b[1]:
                continue
            iou = _iou(a[3:], b[3:])
            if iou > 0.9:
                removed.add(b[0]); dup_idx.append(b[0])
                dup_rows.append({"frame_id": fid, "class_name": a[1], "kept_source_class": a[2],
                                 "removed_source_class": b[2], "iou": round(iou, 3)})
    B = B.drop(dup_idx)
    log.append(("exact duplicate boxes removed (same class, IoU > 0.9)", len(dup_idx),
                ", ".join(f"{r['class_name']} in {r['frame_id']} ({r['removed_source_class']}, IoU {r['iou']})" for r in dup_rows)))

    # 6. metadata normalisation
    F["restaurant_id_source"] = F["restaurant_id"]
    n_rest = int(F.restaurant_id.isin(RESTAURANT_FIX).sum())
    F["restaurant_id"] = F["restaurant_id"].replace(RESTAURANT_FIX)
    log.append(("restaurant_id R01cutte-t -> R01", n_rest, "frames"))
    F["camera"] = F["camera_view"].map(CAMERA)
    F["clip"] = F["clip_ts"]
    F["recording_date"] = pd.to_datetime(F["clip_ts"].str[:8], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    F["recording_start"] = pd.to_datetime(F["clip_ts"].str[:14], format="%Y%m%d%H%M%S").dt.strftime("%Y-%m-%d %H:%M:%S")

    B["class_id"] = B.class_name.map(CLASS_ID).astype(int)
    B = B.merge(F[["frame_id", "clip"]], on="frame_id", how="left")
    F["num_boxes_clean"] = F.frame_id.map(B.groupby("frame_id").size()).fillna(0).astype(int)
    F = F.drop(columns=["_upd"]).reset_index(drop=True)
    B = B.reset_index(drop=True)
    return F, B, pd.DataFrame(log, columns=["step", "count", "detail"]), pd.DataFrame(cd_rows), pd.DataFrame(dup_rows), tiny_rows
