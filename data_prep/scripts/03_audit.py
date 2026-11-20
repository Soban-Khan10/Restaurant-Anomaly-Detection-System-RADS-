#!/usr/bin/env python3
"""Phase 1 steps 3-10: de-duplicate, integrity + consistency checks, near-duplicates,
train/val split proposal, statistics, charts, visual spot check, AUDIT_REPORT.md.
Read-only with respect to the source data. Requires 02_load_frames.py to have run.

Usage: python 03_audit.py <prepared_dir>
"""
import sys, json, random, itertools
from pathlib import Path
from collections import defaultdict, Counter
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont

OUT = Path(sys.argv[1]).resolve()
WORK, AUDIT = OUT / "_work", OUT / "audit"
VIS = AUDIT / "visual_check"; VIS.mkdir(parents=True, exist_ok=True)
CSV = AUDIT / "csv"; CSV.mkdir(parents=True, exist_ok=True)
CH = AUDIT / "charts"; CH.mkdir(parents=True, exist_ok=True)
SEED = 1234

CLASSES = ["person", "head_covered", "head_uncovered", "mask_on", "mask_off", "apron", "no_apron",
           "hand_bare", "hand_gloved", "rte_food", "raw_meat", "smoking", "phone", "bin_closed", "bin_open"]
PAIRS = [("head_covered", "head_uncovered"), ("mask_on", "mask_off"), ("apron", "no_apron"),
         ("hand_gloved", "hand_bare"), ("bin_closed", "bin_open")]
HEADS = {"head_covered", "head_uncovered"}
PERSON_PARTS = ["head_covered", "head_uncovered", "mask_on", "mask_off", "apron", "no_apron", "hand_bare", "hand_gloved"]
HAZ = [f"H-0{i}" for i in range(1, 9)]
HAZ_NAMES = {"H-01": "Missing hair restraint / head cover", "H-02": "Bare-hand contact with RTE food",
             "H-03": "Smoking / vaping in prep area", "H-04": "Phone use during food handling",
             "H-05": "Open / overflowing waste bin", "H-06": "Missing / soiled apron or uniform",
             "H-07": "No hand hygiene: raw meat -> RTE", "H-08": "Missing required face mask"}

R = {}  # results for the report

# ------------------------------------------------------------------ load
fa = pd.read_csv(WORK / "frames_all.csv")
ba = pd.read_csv(WORK / "boxes_all.csv")
ex = pd.read_csv(WORK / "exports.csv")
st = pd.read_csv(WORK / "image_stats.csv")
inv_files = pd.read_csv(AUDIT / "inventory_files.csv")
inv_zips = pd.read_csv(AUDIT / "inventory_zips.csv")
inv_prob = pd.read_csv(AUDIT / "inventory_problems.csv")
load_prob = pd.read_csv(WORK / "load_problems.csv")
for c in ["hazards_marked", "hazards_from_boxes"]:
    fa[c] = fa[c].fillna("")
fa["clip_ts"] = fa["clip_file"].astype(str).str.extract(r"(\d{17})")[0]
fa["fid_session"] = fa["frame_id"].str.split("_").str[2]
fa["fid_view"] = fa["frame_id"].str.split("_").str[1]
fa["fid_restaurant"] = fa["frame_id"].str.split("_").str[0]

# ------------------------------------------------------------------ 3. de-duplicate (by frame_id)
fa["_upd"] = pd.to_datetime(fa["updated_at"], utc=True, errors="coerce")
fa["_exp"] = pd.to_datetime(fa["exported_at"], utc=True, errors="coerce")
fa = fa.sort_values(["frame_id", "_upd", "_exp"], na_position="first").reset_index(drop=True)
keep_idx = fa.groupby("frame_id").tail(1).index
fa["kept"] = fa.index.isin(keep_idx)

boxes_by_uid = {u: g for u, g in ba.groupby("row_uid")}


def boxes_of(uid):
    return boxes_by_uid.get(uid, ba.iloc[0:0])


def compare_boxes(b1, b2):
    """Return (same_classes, max_shift). Greedy matching by class on centre distance."""
    c1, c2 = Counter(b1.class_name), Counter(b2.class_name)
    if c1 != c2:
        return False, None
    mx = 0.0
    for cls in c1:
        a = b1[b1.class_name == cls][["cx", "cy", "bw", "bh"]].values.tolist()
        b = b2[b2.class_name == cls][["cx", "cy", "bw", "bh"]].values.tolist()
        while a:
            p = a.pop(0)
            j = min(range(len(b)), key=lambda k: abs(b[k][0] - p[0]) + abs(b[k][1] - p[1]))
            q = b.pop(j)
            # shift of edges, normalised (fraction of image)
            e1 = [p[0] - p[2] / 2, p[1] - p[3] / 2, p[0] + p[2] / 2, p[1] + p[3] / 2]
            e2 = [q[0] - q[2] / 2, q[1] - q[3] / 2, q[0] + q[2] / 2, q[1] + q[3] / 2]
            mx = max(mx, max(abs(x - y) for x, y in zip(e1, e2)))
    return True, mx


dup_rows = []
for fid, g in fa[fa.frame_id.duplicated(keep=False)].groupby("frame_id"):
    kept = g[g.kept].iloc[0]
    for _, o in g[~g.kept].iterrows():
        same_cls, shift = compare_boxes(boxes_of(kept.row_uid), boxes_of(o.row_uid))
        differ = (not same_cls) or (shift is not None and shift > 0.02)
        dup_rows.append({"frame_id": fid, "kept_export": kept.export, "kept_annotator": kept.annotator,
                         "kept_updated_at": kept.updated_at, "kept_session": kept.session_id, "kept_boxes": kept.num_boxes,
                         "dropped_export": o.export, "dropped_annotator": o.annotator, "dropped_updated_at": o.updated_at,
                         "dropped_session": o.session_id, "dropped_boxes": o.num_boxes,
                         "same_class_list": same_cls, "max_box_shift_frac": None if shift is None else round(shift, 4),
                         "boxes_differ": differ})
dup = pd.DataFrame(dup_rows, columns=["frame_id", "kept_export", "kept_annotator", "kept_updated_at", "kept_session",
                                      "kept_boxes", "dropped_export", "dropped_annotator", "dropped_updated_at",
                                      "dropped_session", "dropped_boxes", "same_class_list", "max_box_shift_frac", "boxes_differ"])
dup.to_csv(CSV / "duplicates_by_frame_id.csv", index=False)

F = fa[fa.kept].copy().reset_index(drop=True)
F = F.merge(st, on="raw_path", how="left")
B = ba[ba.row_uid.isin(set(F.row_uid))].copy()
B = B.merge(F[["row_uid", "meta_width", "meta_height", "annotator", "clip_ts"]], on="row_uid", how="left")
B["bid"] = np.arange(len(B))
W, H = B.meta_width.astype(float), B.meta_height.astype(float)
B["x1"], B["y1"] = B.cx - B.bw / 2, B.cy - B.bh / 2
B["x2"], B["y2"] = B.cx + B.bw / 2, B.cy + B.bh / 2
B["w_px"], B["h_px"] = B.bw * W, B.bh * H
B["in_target"] = B.class_name.isin(CLASSES)

# content duplicates: same image (clip + frame_index, or identical bytes) under different frame_ids
cd = []
for key, g in F.groupby(["clip_ts", "frame_index"]):
    if len(g) > 1:
        cd.append({"kind": "same clip + frame_index", "key": f"{key[0]} f{int(key[1])}",
                   "frame_ids": " ; ".join(g.frame_id), "annotators": " ; ".join(g.annotator),
                   "updated_at": " ; ".join(g.updated_at.astype(str)), "num_boxes": " ; ".join(g.num_boxes.astype(str)),
                   "identical_bytes": g.sha1.nunique() == 1})
for key, g in F.groupby("sha1"):
    if len(g) > 1 and g[["clip_ts", "frame_index"]].drop_duplicates().shape[0] > 1:
        cd.append({"kind": "identical image bytes", "key": key[:12], "frame_ids": " ; ".join(g.frame_id),
                   "annotators": " ; ".join(g.annotator), "updated_at": " ; ".join(g.updated_at.astype(str)),
                   "num_boxes": " ; ".join(g.num_boxes.astype(str)), "identical_bytes": True})
content_dup = pd.DataFrame(cd, columns=["kind", "key", "frame_ids", "annotators", "updated_at", "num_boxes", "identical_bytes"])
content_dup.to_csv(CSV / "content_duplicates_different_frame_id.csv", index=False)


# ------------------------------------------------------------------ 4. integrity
def iou_mat(a, b):
    ix1 = np.maximum(a[:, None, 0], b[None, :, 0]); iy1 = np.maximum(a[:, None, 1], b[None, :, 1])
    ix2 = np.minimum(a[:, None, 2], b[None, :, 2]); iy2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
    aa = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1]); ab = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(aa[:, None] + ab[None, :] - inter, 1e-12), inter, aa, ab


issues = {}  # name -> DataFrame
img_missing = F[F.exists != True]
img_unread = F[(F.exists == True) & (F.readable != True)]
img_size = F[(F.readable == True) & ((F.real_width != F.meta_width) | (F.real_height != F.meta_height))]
issues["raw_image_missing"] = img_missing[["frame_id", "export", "raw_path"]]
issues["raw_image_unreadable"] = img_unread[["frame_id", "export", "raw_path", "error"]]
issues["image_size_mismatch"] = img_size[["frame_id", "export", "meta_width", "meta_height", "real_width", "real_height"]]
ann_missing = F[~F.annotated_path.fillna("").apply(lambda p: Path(p).exists() if p else False)]
issues["annotated_image_missing"] = ann_missing[["frame_id", "export"]]

bad_vals = B[(B[["cx", "cy", "bw", "bh"]].isna().any(axis=1)) | (B.cx < 0) | (B.cx > 1) | (B.cy < 0) | (B.cy > 1) |
             (B.bw <= 0) | (B.bh <= 0) | (B.bw > 1) | (B.bh > 1)]
issues["bbox_values_invalid"] = bad_vals[["frame_id", "export", "class_name", "cx", "cy", "bw", "bh"]]
eps = 1e-6
outside = B[(B.x1 < -eps) | (B.y1 < -eps) | (B.x2 > 1 + eps) | (B.y2 > 1 + eps)]
issues["box_partly_outside_image"] = outside[["frame_id", "export", "class_name", "x1", "y1", "x2", "y2"]]
tiny = B[(B.w_px < 4) | (B.h_px < 4)]
issues["tiny_box_lt_4px"] = tiny[["frame_id", "export", "annotator", "class_name", "w_px", "h_px"]]
# yolo vs pixel consistency (extra check)
pxd = np.maximum.reduce([abs(B.px_x - B.x1 * W), abs(B.px_y - B.y1 * H), abs(B.px_w - B.w_px), abs(B.px_h - B.h_px)])
pxbad = B[pxd > 3]
issues["bbox_yolo_vs_pixel_mismatch_gt3px"] = pxbad[["frame_id", "export", "class_name"]].assign(max_diff_px=pxd[pxd > 3])

exact_dup, conflicts, part_orphans, person_nohead = [], [], [], []
person_total = 0
conf_set = {frozenset(p) for p in PAIRS}
for uid, g in B.groupby("row_uid"):
    fid = g.frame_id.iloc[0]
    xy = g[["x1", "y1", "x2", "y2"]].values.astype(float)
    cls = g.class_name.values
    if len(g) > 1:
        iou, inter, aa, ab = iou_mat(xy, xy)
        for i, j in itertools.combinations(range(len(g)), 2):
            if cls[i] == cls[j] and iou[i, j] > 0.9:
                exact_dup.append({"frame_id": fid, "class_name": cls[i], "iou": round(iou[i, j], 3),
                                  "box_a": int(g.box_idx.iloc[i]), "box_b": int(g.box_idx.iloc[j])})
            if frozenset((cls[i], cls[j])) in conf_set and iou[i, j] > 0.5:
                conflicts.append({"frame_id": fid, "annotator": g.annotator.iloc[0], "class_a": cls[i], "class_b": cls[j],
                                  "iou": round(iou[i, j], 3)})
    # consistency: containment = share of box j inside box i
    pm = cls == "person"
    persons = xy[pm]; person_total += pm.sum()
    if len(g):
        _, inter, aa, ab = iou_mat(xy, xy)
        frac_inside = inter / np.maximum(ab[None, :], 1e-12)  # [i, j] = share of j inside i
        heads = np.isin(cls, list(HEADS))
        for i in np.where(pm)[0]:
            if not any(frac_inside[i, j] >= 0.6 for j in np.where(heads)[0]):
                person_nohead.append({"frame_id": fid, "annotator": g.annotator.iloc[0], "box_idx": int(g.box_idx.iloc[i]),
                                      "person_h_px": round(float(g.h_px.iloc[i]), 1),
                                      "touches_image_edge": bool(xy[i, 0] < 0.01 or xy[i, 1] < 0.01 or xy[i, 2] > 0.99 or xy[i, 3] > 0.99)})
        for j in np.where(np.isin(cls, PERSON_PARTS))[0]:
            if not any(frac_inside[i, j] >= 0.6 for i in np.where(pm)[0]):
                part_orphans.append({"frame_id": fid, "annotator": g.annotator.iloc[0], "class_name": cls[j],
                                     "box_idx": int(g.box_idx.iloc[j])})
issues["exact_duplicate_boxes"] = pd.DataFrame(exact_dup, columns=["frame_id", "class_name", "iou", "box_a", "box_b"])
issues["conflicting_labels"] = pd.DataFrame(conflicts, columns=["frame_id", "annotator", "class_a", "class_b", "iou"])
other = B[~B.in_target]
issues["other_class_boxes"] = other[["frame_id", "export", "annotator", "class_name", "src_class_id"]]
dark = F[(F.readable == True) & ((F.mean_brightness < 10) | (F.std_brightness < 2))]
issues["dark_or_flat_images"] = dark[["frame_id", "mean_brightness", "std_brightness"]]
nohead = pd.DataFrame(person_nohead, columns=["frame_id", "annotator", "box_idx", "person_h_px", "touches_image_edge"])
orph = pd.DataFrame(part_orphans, columns=["frame_id", "annotator", "class_name", "box_idx"])
nohead.to_csv(CSV / "persons_without_head.csv", index=False)
orph.to_csv(CSV / "parts_outside_any_person.csv", index=False)

# metadata consistency (extra): session/view in recording vs frame_id, restaurant typos, background flag
meta_inc = []
for _, r in F.iterrows():
    if r.fid_session != r.session_id: meta_inc.append((r.frame_id, "session_id differs from frame_id", r.session_id, r.fid_session))
    if r.fid_view != r.camera_view: meta_inc.append((r.frame_id, "camera_view differs from frame_id", r.camera_view, r.fid_view))
    if r.restaurant_id != "R01" and not str(r.restaurant_id).startswith("R0") or len(str(r.restaurant_id)) != 3:
        meta_inc.append((r.frame_id, "restaurant_id malformed", r.restaurant_id, r.fid_restaurant))
    if bool(r.is_background) != (r.num_boxes == 0): meta_inc.append((r.frame_id, "is_background flag vs box count", r.is_background, r.num_boxes))
meta_inc = pd.DataFrame(meta_inc, columns=["frame_id", "issue", "metadata_value", "other_value"])
meta_inc.to_csv(CSV / "metadata_inconsistencies.csv", index=False)

# ------------------------------------------------------------------ 5. hazards vs boxes
haz_rows, haz_mis = [], []
bx_by_fid = {f: g for f, g in B.groupby("frame_id")}
for _, r in F.iterrows():
    g = bx_by_fid.get(r.frame_id, B.iloc[0:0])
    marked = set(filter(None, r.hazards_marked.split("|")))
    any_box = set(g.hazard.dropna())
    viol = set(g[g.status == "violation"].hazard.dropna())
    row = {"frame_id": r.frame_id}
    for h in HAZ:
        row[f"{h}_marked"] = h in marked; row[f"{h}_violation_box"] = h in viol
    haz_rows.append(row)
    for h in sorted(marked - any_box):
        haz_mis.append({"frame_id": r.frame_id, "annotator": r.annotator, "hazard": h,
                        "problem": "hazard marked but no box of that hazard"})
    for h in sorted(viol - marked):
        haz_mis.append({"frame_id": r.frame_id, "annotator": r.annotator, "hazard": h,
                        "problem": "violation box present but hazard not marked"})
HZ = pd.DataFrame(haz_rows)
haz_mis = pd.DataFrame(haz_mis, columns=["frame_id", "annotator", "hazard", "problem"])
haz_mis.to_csv(CSV / "hazard_box_mismatch.csv", index=False)

# ------------------------------------------------------------------ 6. near-duplicates (phash, Hamming <= 4, per clip)
def hbits(h):
    return np.array([int(c) for c in bin(int(h, 16))[2:].zfill(64)], dtype=np.uint8)


F["nd_cluster"] = ""
nd_rows, hash_rows = [], []
for clip, g in F.groupby("clip_ts"):
    idx = g.index.tolist()
    bits = np.stack([hbits(h) for h in g.phash])
    d = (bits[:, None, :] != bits[None, :, :]).sum(-1)
    parent = list(range(len(idx)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for i, j in zip(*np.where(np.triu(d <= 4, 1))):
        parent[find(i)] = find(j)
    roots = [find(i) for i in range(len(idx))]
    sizes = Counter(roots)
    for k, i in enumerate(idx):
        F.at[i, "nd_cluster"] = f"{clip}_c{roots[k]}"
    multi = [s for s in sizes.values() if s > 1]
    nd_rows.append({"clip_ts": clip, "frames": len(idx), "clusters_total": len(sizes),
                    "clusters_with_2plus": len(multi), "frames_in_2plus_clusters": sum(multi),
                    "largest_cluster": max(sizes.values()),
                    "near_dup_frames_beyond_first": sum(multi) - len(multi)})
ND = pd.DataFrame(nd_rows)
ND.to_csv(CSV / "near_duplicates_per_clip.csv", index=False)
F[["frame_id", "phash"]].to_csv(AUDIT / "train_hashes.csv", index=False)
F[["frame_id", "clip_ts", "frame_index", "nd_cluster"]].to_csv(CSV / "near_duplicate_clusters.csv", index=False)

# ------------------------------------------------------------------ 7. split proposal
Bt = B[B.in_target]
# (a) table by labelled (restaurant_id, session_id), for information
lab = Bt.merge(F[["frame_id", "restaurant_id", "session_id"]], on="frame_id")
lab_tab = lab.pivot_table(index=["restaurant_id", "session_id"], columns="class_name", values="bid", aggfunc="count", fill_value=0)
lab_tab = lab_tab.reindex(columns=CLASSES, fill_value=0)
fr_lab = F.groupby(["restaurant_id", "session_id"]).agg(frames=("frame_id", "count"),
                                                         camera_views=("camera_view", lambda x: ",".join(sorted(set(x)))),
                                                         clips=("clip_ts", "nunique"))
lab_tab = fr_lab.join(lab_tab).fillna(0)
lab_tab.to_csv(CSV / "groups_by_labelled_session.csv")

# (b) proposed group = source video clip (17-digit recording timestamp in the clip file name)
F["group"] = F["clip_ts"]
grp_frames = F.groupby("group").agg(frames=("frame_id", "count"),
                                    camera_views=("camera_view", lambda x: ",".join(sorted(set(x)))),
                                    session_labels=("session_id", lambda x: ",".join(sorted(set(x)))),
                                    annotators=("annotator", lambda x: ",".join(sorted(set(a.split()[0] for a in x)))),
                                    background=("num_boxes", lambda x: int((x == 0).sum())))
gb = Bt.merge(F[["frame_id", "group"]], on="frame_id").pivot_table(index="group", columns="class_name", values="bid",
                                                                   aggfunc="count", fill_value=0)
gb = gb.reindex(index=grp_frames.index, columns=CLASSES, fill_value=0).fillna(0).astype(int)
G = grp_frames.join(gb)
G.index.name = "group (clip recording timestamp)"
G.to_csv(CSV / "groups_by_clip.csv")

groups = G.index.tolist(); n = len(groups)
fr = G.frames.values.astype(np.int64); cb = G[CLASSES].values.astype(np.int64)
tot_fr, tot_cb = fr.sum(), cb.sum(0)
best = None
masks = np.arange(1, 2 ** n - 1, dtype=np.int64)
CH_SZ = 1 << 16
for s in range(0, len(masks), CH_SZ):
    m = masks[s:s + CH_SZ]
    sel = ((m[:, None] >> np.arange(n)) & 1).astype(np.int64)  # integer maths (no BLAS)
    vf = (sel * fr[None, :]).sum(1) / tot_fr
    vb = np.stack([(sel * cb[None, :, k]).sum(1) for k in range(cb.shape[1])], 1)
    ok = (vf >= 0.12) & (vf <= 0.18)
    if not ok.any():
        continue
    share = vb / np.maximum(tot_cb, 1)
    train_major = (share <= 0.5).all(1)  # train keeps the majority of every class
    capped = np.minimum(vb, 20)
    n20 = (vb >= 20).sum(1)
    score = n20 * 1e9 + capped.sum(1) * 1e5 + vb.min(1) * 1e2 - abs(vf - 0.15) * 1e2 + train_major * 1e12
    score[~ok] = -np.inf
    k = int(np.argmax(score))
    if best is None or score[k] > best[0]:
        best = (score[k], int(m[k]))
val_groups = [groups[i] for i in range(n) if (best[1] >> i) & 1]
F["proposed_split"] = np.where(F.group.isin(val_groups), "val", "train")
B = B.merge(F[["frame_id", "proposed_split"]], on="frame_id", how="left")
Bt = B[B.in_target]

# (c) fallback: time-based within each clip (shown for comparison)
fb = []
for clip, g in F.groupby("clip_ts"):
    t = g.timestamp_sec.astype(float)
    dur = g.clip_duration_sec.dropna().max()
    t_end = dur if pd.notna(dur) and dur > 0 else t.max()
    t_start = min(t.min(), 0)
    val_start = t_end - 0.15 * (t_end - t_start)
    for i, ti in zip(g.index, t):
        fb.append((i, "val" if ti >= val_start else ("train" if ti < val_start - 5 else "gap_excluded")))
for i, s in fb:
    F.at[i, "fallback_split"] = s
# near-dup clusters crossing sides in the fallback
cross = F[F.fallback_split != "gap_excluded"].groupby("nd_cluster").fallback_split.nunique()
fb_cross = int((cross > 1).sum())

F[["frame_id", "group", "proposed_split", "fallback_split", "session_id", "camera_view", "clip_file"]].to_csv(
    CSV / "proposed_split_per_frame.csv", index=False)


def split_table(col):
    rows = []
    for s in ["train", "val"] + (["gap_excluded"] if col == "fallback_split" else []):
        fids = set(F[F[col] == s].frame_id)
        bb = Bt[Bt.frame_id.isin(fids)]
        row = {"split": s, "frames": len(fids), "background_frames": int((F[F[col] == s].num_boxes == 0).sum()),
               "boxes": len(bb)}
        row.update(bb.class_name.value_counts().reindex(CLASSES, fill_value=0).to_dict())
        rows.append(row)
    return pd.DataFrame(rows).set_index("split")


ST = split_table("proposed_split"); ST.to_csv(CSV / "proposed_split_summary.csv")
FBT = split_table("fallback_split"); FBT.to_csv(CSV / "fallback_split_summary.csv")
weak_val = [c for c in CLASSES if ST.loc["val", c] < 20]
weak_val_fb = [c for c in CLASSES if FBT.loc["val", c] < 20]

# ------------------------------------------------------------------ 8. statistics
S = {}
def vc(s): return s.value_counts().rename_axis(s.name).reset_index(name="frames")
S["frames_per_annotator"] = vc(F.annotator)
S["frames_per_restaurant"] = vc(F.restaurant_id)
S["frames_per_session_label"] = vc(F.session_id)
S["frames_per_camera_view"] = vc(F.camera_view)
S["frames_per_clip"] = F.groupby(["clip_ts", "clip_file"]).size().reset_index(name="frames") \
    if False else F.groupby("clip_ts").agg(clip_files=("clip_file", lambda x: " | ".join(sorted(set(x)))),
                                           frames=("frame_id", "count")).reset_index()
S["image_resolutions"] = F.assign(res=F.real_width.astype("Int64").astype(str) + "x" + F.real_height.astype("Int64").astype(str)).res.value_counts().rename_axis("resolution").reset_index(name="frames")


def per_split(fn):
    out = {}
    for name, sub in [("overall", F), ("train", F[F.proposed_split == "train"]), ("val", F[F.proposed_split == "val"])]:
        out[name] = fn(sub, Bt[Bt.frame_id.isin(set(sub.frame_id))])
    return pd.DataFrame(out)


S["boxes_per_class"] = per_split(lambda f, b: b.class_name.value_counts().reindex(CLASSES, fill_value=0))
S["frames_containing_class"] = per_split(lambda f, b: b.groupby("class_name").frame_id.nunique().reindex(CLASSES, fill_value=0))
HZ2 = HZ.merge(F[["frame_id", "proposed_split"]], on="frame_id")
hz_rows = []
for h in HAZ:
    row = {"hazard": h, "name": HAZ_NAMES[h]}
    for name, sub in [("overall", HZ2), ("train", HZ2[HZ2.proposed_split == "train"]), ("val", HZ2[HZ2.proposed_split == "val"])]:
        row[f"{name}_marked"] = int(sub[f"{h}_marked"].sum())
        row[f"{name}_violation_box"] = int(sub[f"{h}_violation_box"].sum())
        row[f"{name}_either"] = int((sub[f"{h}_marked"] | sub[f"{h}_violation_box"]).sum())
    hz_rows.append(row)
S["frames_per_hazard"] = pd.DataFrame(hz_rows)
pr = []
for a, b in PAIRS:
    row = {"pair": f"{a} / {b}"}
    for name in ["overall", "train", "val"]:
        na, nb = S["boxes_per_class"].loc[a, name], S["boxes_per_class"].loc[b, name]
        t = na + nb
        row[name] = (f"{a} {na} ({100 * na / t:.1f}%) · {b} {nb} ({100 * nb / t:.1f}%) · ratio "
                     + (f"{max(na, nb) / min(na, nb):.1f} : 1" if min(na, nb) else "n/a")) if t else "none"
    pr.append(row)
S["paired_classes"] = pd.DataFrame(pr)
bpc = S["boxes_per_class"]["overall"]
imb = {"largest": bpc.idxmax(), "largest_n": int(bpc.max()), "smallest": bpc.idxmin(), "smallest_n": int(bpc.min()),
       "ratio": bpc.max() / max(bpc.min(), 1)}
lt100_train = [c for c in CLASSES if S["boxes_per_class"].loc[c, "train"] < 100]
# box size at training scale
scale = 640 / np.maximum(Bt.meta_width.astype(float), Bt.meta_height.astype(float))
Bt = Bt.assign(w640=Bt.w_px * scale, h640=Bt.h_px * scale)
Bt = Bt.assign(short640=np.minimum(Bt.w640, Bt.h640))
bs = []
for c in CLASSES:
    g = Bt[Bt.class_name == c]
    if not len(g):
        bs.append({"class": c, "boxes": 0}); continue
    bs.append({"class": c, "boxes": len(g),
               "median_w": round(g.w640.median(), 1), "median_h": round(g.h640.median(), 1),
               "p10_w": round(g.w640.quantile(.1), 1), "p10_h": round(g.h640.quantile(.1), 1),
               "pct_short_lt16": round(100 * (g.short640 < 16).mean(), 1),
               "pct_short_lt8": round(100 * (g.short640 < 8).mean(), 1)})
S["box_size_at_640"] = pd.DataFrame(bs)
hard_small = S["box_size_at_640"][S["box_size_at_640"].pct_short_lt16 > 25]["class"].tolist()
S["annotator_x_class"] = Bt.pivot_table(index="annotator", columns="class_name", values="bid", aggfunc="count",
                                         fill_value=0).reindex(columns=CLASSES, fill_value=0)
for k, v in S.items():
    v.to_csv(CSV / f"stats_{k}.csv", index=k in ("boxes_per_class", "frames_containing_class", "annotator_x_class"))

# charts
plt.figure(figsize=(10, 5)); bpc.plot.bar(color="#4C72B0"); plt.title("Boxes per class (all frames)")
plt.ylabel("boxes"); plt.tight_layout(); plt.savefig(CH / "boxes_per_class.png", dpi=120); plt.close()
S["boxes_per_class"][["train", "val"]].plot.bar(figsize=(11, 5), color=["#4C72B0", "#DD8452"])
plt.title("Boxes per class: proposed train vs val"); plt.ylabel("boxes"); plt.yscale("log"); plt.tight_layout()
plt.savefig(CH / "boxes_per_class_train_val.png", dpi=120); plt.close()
plt.figure(figsize=(12, 5))
plt.boxplot([Bt[Bt.class_name == c].short640.values for c in CLASSES], tick_labels=CLASSES, showfliers=False)
plt.axhline(16, color="r", ls="--", lw=1, label="16 px"); plt.axhline(8, color="orange", ls="--", lw=1, label="8 px")
plt.yscale("log"); plt.ylabel("shorter side at 640 px (log)"); plt.xticks(rotation=45, ha="right"); plt.legend()
plt.title("Box size per class at training scale (longest image side = 640)"); plt.tight_layout()
plt.savefig(CH / "box_size_per_class.png", dpi=120); plt.close()

# ------------------------------------------------------------------ 9. visual spot check (from bbox_yolo)
rng = random.Random(SEED)
cand = F[F.readable == True]
chosen = []
anns = sorted(cand.annotator.unique())
for i, c in enumerate(CLASSES + sorted(set(other.class_name))):
    a = anns[i % len(anns)]
    pool = Bt[(Bt.class_name == c)] if c in CLASSES else other[other.class_name == c]
    pool_f = [f for f in pool.frame_id.unique() if f not in chosen]
    pa = [f for f in pool_f if f in set(cand[cand.annotator == a].frame_id)] or pool_f
    if pa:
        chosen.append(rng.choice(sorted(pa)))
bg = sorted(cand[cand.num_boxes == 0].frame_id)
if bg: chosen.append(rng.choice(bg))
while len(chosen) < 30:
    a = anns[len(chosen) % len(anns)]
    pool = sorted(set(cand[cand.annotator == a].frame_id) - set(chosen))
    chosen.append(rng.choice(pool))
palette = {c: tuple(int(x * 255) for x in plt.cm.tab20(i % 20)[:3]) for i, c in enumerate(CLASSES)}
try:
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
except Exception:
    font = ImageFont.load_default()
vis_rows = []
for fid in chosen:
    r = F[F.frame_id == fid].iloc[0]
    im = Image.open(r.raw_path).convert("RGB"); w, h = im.size
    d = ImageDraw.Draw(im)
    g = B[B.frame_id == fid]
    for _, b in g.iterrows():
        x1, y1, x2, y2 = (b.cx - b.bw / 2) * w, (b.cy - b.bh / 2) * h, (b.cx + b.bw / 2) * w, (b.cy + b.bh / 2) * h
        col = palette.get(b.class_name, (255, 0, 255))
        d.rectangle([x1, y1, x2, y2], outline=col, width=4)
        d.rectangle([x1, max(0, y1 - 26), x1 + 12 * len(b.class_name) + 10, max(0, y1 - 26) + 26], fill=col)
        d.text((x1 + 4, max(0, y1 - 26) + 1), b.class_name, fill=(0, 0, 0), font=font)
    d.rectangle([0, 0, w, 34], fill=(0, 0, 0))
    d.text((8, 5), f"{fid} | {r.annotator} | {len(g)} boxes (drawn from bbox_yolo)", fill=(255, 255, 255), font=font)
    im.resize((w // 2, h // 2)).save(VIS / f"{fid}.jpg", quality=85)
    vis_rows.append({"frame_id": fid, "annotator": r.annotator, "boxes": len(g),
                     "classes": ",".join(sorted(set(g.class_name)))})
pd.DataFrame(vis_rows).to_csv(VIS / "_index.csv", index=False)

# ------------------------------------------------------------------ save cleaned tables for phase 2
F.to_csv(WORK / "frames_kept.csv", index=False)
B.to_csv(WORK / "boxes_kept.csv", index=False)
for k, v in issues.items():
    v.to_csv(CSV / f"issue_{k}.csv", index=False)

# ------------------------------------------------------------------ 10. report
def md(df, index=False):
    df = df.copy()
    cols = ([df.index.name or ""] if index else []) + [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for idx, row in df.iterrows():
        vals = ([str(idx)] if index else []) + [("" if pd.isna(v) else str(v)) for v in row.values]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def ex_ids(df, col="frame_id", k=3):
    ids = list(dict.fromkeys(df[col].tolist()))
    return ", ".join(f"`{i}`" for i in ids[:k]) + (" …" if len(ids) > k else "")


n_frames, n_boxes_t = len(F), len(Bt)
n_bg = int((F.num_boxes == 0).sum())
L = []
L += ["# RADS dataset audit report (Phase 1, read-only)", "",
      f"Generated by `scripts/03_audit.py`. Source: `All_Yolo_Data` (not modified). All detail CSVs are in `audit/csv/`.", ""]
L += ["## 1. What was found", "",
      f"- Files in source tree (incl. inside ZIPs): **{len(inv_files)}**; ZIPs found: **{len(inv_zips)}** "
      f"(nested ZIPs: {int(inv_files[inv_files.origin != 'source'].path.str.lower().str.endswith('.zip').sum())}); "
      f"unreadable ZIPs/files: **{len(inv_prob)}**; unreadable JSON: **{len(load_prob[load_prob.kind.str.contains('unreadable')]) if len(load_prob) else 0}**.",
      f"- Exports: **{len(ex)}** (all in the new `frames/` layout; none in the old `images/` layout).",
      f"- Frame entries loaded: **{len(fa)}** → unique frame_ids: **{n_frames}**.",
      f"- Boxes on kept frames: **{len(B)}** in total, of which **{n_boxes_t}** belong to the 15 target classes "
      f"and **{len(other)}** to other classes.",
      f"- Background frames (0 boxes): **{n_bg}** ({100 * n_bg / n_frames:.1f}%).",
      f"- Annotators: " + ", ".join(f"{a} ({c})" for a, c in F.annotator.value_counts().items()) + ".",
      f"- Restaurants: {', '.join(f'`{a}` ({c})' for a, c in F.restaurant_id.value_counts().items())}; "
      f"camera views: {', '.join(f'{a} ({c})' for a, c in F.camera_view.value_counts().items())}; "
      f"source clips: **{F.clip_ts.nunique()}**; image resolution: "
      + ", ".join(f"{r.resolution} ({r.frames})" for r in S["image_resolutions"].itertuples()) + ".", ""]
L += ["### Exports", "", md(ex[["export", "exported_by", "exported_at", "part", "parts_total", "frames_in_annotations_json",
                                  "frame_folders", "frames_loaded"]]), ""]

# ranked issues
other_cnt = other.class_name.value_counts()
nohead_share = 100 * len(nohead) / max(person_total, 1)
L += ["## 2. Issues ranked by importance", ""]
iss = []
iss.append(("HIGH", "Other (non-target) class names in boxes",
            f"{len(other)} boxes: " + ", ".join(f"`{k}` {v}" for k, v in other_cnt.items()) if len(other) else "none",
            ex_ids(other) if len(other) else "", "Needs your decision (see section 3)."))
sess_mis = meta_inc[meta_inc.issue.str.startswith("session")]
iss.append(("HIGH", "Session labels are not usable as recording groups",
            f"{len(sess_mis)} frames have a `session_id` different from the session in their own frame_id; "
            f"{int((F.groupby('clip_ts').session_id.nunique() > 1).sum())} clips carry 2+ different session labels; "
            f"each annotator used their own numbering (" +
            "; ".join(f"{a.split()[0]}: {','.join(sorted(set(g.session_id)))}" for a, g in F.groupby("annotator")) + ").",
            ex_ids(sess_mis), "Split proposed by source video clip instead (section 7)."))
iss.append(("HIGH", "All clips come from one camera on two evenings",
            f"{F.clip_ts.nunique()} clips, all with camera_id {', '.join(sorted(set(F.camera_id.astype(str))))}, recorded on "
            f"{', '.join(sorted(set(F.clip_ts.str[:8])))} (from clip file names). Val frames will show the same scene/people as train.",
            "", "Not fixable here; the separately recorded test set is what measures generalisation."))
iss.append(("MEDIUM", "Conflicting labels (mutually exclusive classes, IoU > 0.5)",
            f"{len(issues['conflicting_labels'])} pairs in {issues['conflicting_labels'].frame_id.nunique()} frames: " +
            ", ".join(f"{a}/{b} {c}" for (a, b), c in issues['conflicting_labels'].groupby(['class_a', 'class_b']).size().items()),
            ex_ids(issues["conflicting_labels"]), "Needs your decision."))
iss.append(("MEDIUM", "Persons without a head box",
            f"{len(nohead)} of {person_total} person boxes ({nohead_share:.1f}%); "
            f"{int(nohead.touches_image_edge.sum())} of them touch the image edge (head may be out of view)",
            ex_ids(nohead), "Report only."))
iss.append(("MEDIUM", "Person-part boxes not inside any person box",
            f"{len(orph)} boxes: " + ", ".join(f"{k} {v}" for k, v in orph.class_name.value_counts().items()),
            ex_ids(orph), "Report only (possible missed person boxes)."))
iss.append(("MEDIUM", "Hazard tags disagree with boxes",
            f"{len(haz_mis)} cases in {haz_mis.frame_id.nunique()} frames: " +
            ", ".join(f"{p} {h}: {c}" for (p, h), c in haz_mis.groupby(['problem', 'hazard']).size().items()),
            ex_ids(haz_mis), "Report only; hazard tags are not used for training."))
iss.append(("MEDIUM", "Same image saved under different frame_ids",
            f"{len(content_dup)} cases (not caught by frame_id de-duplication)", ex_ids(content_dup, "frame_ids", 2),
            "Proposed: keep only the copy with latest updated_at in Phase 2 (both are in the same clip, so no split leak)."))
iss.append(("LOW", "Duplicate frame_ids across/within exports", f"{len(dup)} extra copies; "
            f"{int(dup.boxes_differ.sum()) if len(dup) else 0} with different boxes", ex_ids(dup) if len(dup) else "",
            "Kept the latest `updated_at`."))
iss.append(("LOW", "Tiny boxes (< 4 px in original image)", f"{len(tiny)}", ex_ids(tiny) if len(tiny) else "", "Default: drop."))
iss.append(("LOW", "Exact duplicate boxes (same class, IoU > 0.9)", f"{len(issues['exact_duplicate_boxes'])}",
            ex_ids(issues["exact_duplicate_boxes"]) if len(issues["exact_duplicate_boxes"]) else "", "Will be removed in Phase 2."))
iss.append(("LOW", "Boxes partly outside the image", f"{len(outside)}", ex_ids(outside) if len(outside) else "", "Will be clipped in Phase 2."))
iss.append(("LOW", "Invalid bbox_yolo values (outside 0–1 or w/h ≤ 0)", f"{len(bad_vals)}", ex_ids(bad_vals) if len(bad_vals) else "", ""))
iss.append(("LOW", "bbox_yolo vs pixel box differ by > 3 px", f"{len(pxbad)}", ex_ids(pxbad) if len(pxbad) else "", ""))
vmis = meta_inc[meta_inc.issue.str.startswith("camera_view")]
iss.append(("LOW", "camera_view differs from frame_id", f"{len(vmis)} frames", ex_ids(vmis) if len(vmis) else "", "Metadata value used."))
rmis = meta_inc[meta_inc.issue.str.startswith("restaurant")]
iss.append(("LOW", "Malformed restaurant_id", f"{len(rmis)} frames: " + ", ".join(f"`{v}`" for v in rmis.metadata_value.unique()),
            ex_ids(rmis) if len(rmis) else "", "Probably a typo for R01 (same camera); report only."))
iss.append(("LOW", "Raw image missing / unreadable / wrong size",
            f"{len(img_missing)} / {len(img_unread)} / {len(img_size)}", "", ""))
iss.append(("LOW", "Annotated image missing", f"{len(ann_missing)}", "", "Not needed for training."))
iss.append(("LOW", "Dark or flat images (mean < 10 or std < 2)", f"{len(dark)}", ex_ids(dark) if len(dark) else "", ""))
L += [md(pd.DataFrame(iss, columns=["Priority", "Issue", "Count / detail", "Example frame_ids", "Action"])), ""]

L += ["## 3. Other-classes safety check", ""]
if len(other):
    L += ["**Other classes found.** Boxes with a class name that is not one of the 15 target classes "
          "(nothing has been changed or dropped):", ""]
    for c, g in other.groupby("class_name"):
        L += [f"- `{c}` (annotator id {int(g.src_class_id.iloc[0])}): **{len(g)} boxes** in {g.frame_id.nunique()} frames "
              f"(annotators: {', '.join(sorted(set(g.annotator)))}): " + ", ".join(f"`{f}`" for f in g.frame_id.unique())]
    L += ["", "Full list: `audit/csv/issue_other_class_boxes.csv`.", ""]
else:
    L += ["No other classes found.", ""]

L += ["## 4. De-duplication", "",
      f"- frame_id duplicates: **{len(dup)}** extra copies of {dup.frame_id.nunique() if len(dup) else 0} frame_ids; "
      f"kept the copy with the latest `annotation.updated_at`.", ""]
if len(dup):
    L += [md(dup[["frame_id", "kept_export", "kept_annotator", "dropped_export", "dropped_annotator", "kept_session",
                  "dropped_session", "kept_boxes", "dropped_boxes", "same_class_list", "max_box_shift_frac", "boxes_differ"]]), ""]
if len(content_dup):
    L += ["Same image under different frame_ids (all others are distinct):", "",
          md(content_dup[["kind", "key", "frame_ids", "annotators", "num_boxes", "identical_bytes"]]), ""]

L += ["## 5. Integrity and consistency details", "",
      f"- Conflicting labels by pair: " + (", ".join(f"{a} vs {b}: {c}" for (a, b), c in issues['conflicting_labels'].groupby(['class_a', 'class_b']).size().items()) or "none")
      + f"; by annotator: " + (", ".join(f"{a}: {c}" for a, c in issues['conflicting_labels'].annotator.value_counts().items()) or "none") + ".",
      f"- Persons without head: {len(nohead)}/{person_total} ({nohead_share:.1f}%); by annotator: " +
      ", ".join(f"{a}: {c}" for a, c in nohead.annotator.value_counts().items()) + ".",
      f"- Parts outside persons, by annotator: " + ", ".join(f"{a}: {c}" for a, c in orph.annotator.value_counts().items()) + ".",
      f"- Background frames whose `is_background` flag disagrees with box count: {int((meta_inc.issue == 'is_background flag vs box count').sum())}.",
      "", "Lists: `audit/csv/issue_*.csv`, `persons_without_head.csv`, `parts_outside_any_person.csv`, "
      "`hazard_box_mismatch.csv`, `metadata_inconsistencies.csv`.", ""]

L += ["## 6. Near-duplicate frames (phash, Hamming ≤ 4, per clip)", "",
      f"Total: {int(ND.frames_in_2plus_clusters.sum())} of {n_frames} frames sit in a cluster of 2+ near-identical frames "
      f"({int(ND.near_dup_frames_beyond_first.sum())} beyond the first of each cluster). Static CCTV makes this expected. "
      "Clusters never cross clips, so a split by clip keeps every cluster on one side automatically.", "",
      md(ND), "", "Hashes of all frames: `audit/train_hashes.csv`.", ""]

L += ["## 7. Train / val split proposal", "",
      "**Annotator `split` values (information only, not used):** " +
      ", ".join(f"`{k}` {v}" for k, v in F.split.value_counts().items()) + ".", "",
      "### Why not group by (restaurant_id, session_id)", "",
      "The instructions say to group by `(restaurant_id, session_id)`. Here that does not work. Each annotator numbered "
      "sessions independently, and `S01` appears to be a default that was often left unchanged, so one video clip can carry "
      "several session labels and one label (`S01`) covers clips from both evenings. Merging labels that share a clip "
      "collapses almost everything into one group. Table by labelled session (for information): `audit/csv/groups_by_labelled_session.csv`.", "",
      md(lab_tab.reset_index()[["restaurant_id", "session_id", "frames", "camera_views", "clips"]]), "",
      "### Proposed: split by whole source video clip", "",
      f"A group = one source video clip (identified by the 17-digit recording timestamp in its file name). "
      f"There are {n} clips. A clip is never split, and near-duplicate clusters never cross clips. "
      f"Val clips were chosen by exhaustive search over all {2 ** n - 2:,} clip subsets: val share 12–18% of frames, "
      f"maximise the number of classes with ≥ 20 val boxes, then the boxes of the weakest classes, while train keeps "
      f"the majority of every class.", "",
      f"**Val clips ({len(val_groups)}):** " + ", ".join(f"`{g}`" for g in val_groups), "",
      md(ST.reset_index()), "",
      f"Val share: **{100 * ST.loc['val', 'frames'] / n_frames:.1f}%** of frames, "
      f"{100 * ST.loc['val', 'boxes'] / n_boxes_t:.1f}% of boxes.", "",
      ("**Classes with fewer than 20 boxes in val:** " + ", ".join(f"{c} ({ST.loc['val', c]})" for c in weak_val))
      if weak_val else "Every class has at least 20 boxes in val.", "",
      "Per-clip table (frames and boxes per class): `audit/csv/groups_by_clip.csv`.", "",
      md(G.reset_index()), "",
      "### Fallback (time-based within each clip), for comparison", "",
      "The last 15% of each clip's timeline goes to val. Frames in the 5 s before the val start are excluded, so neighbouring "
      "near-identical frames are never on both sides. Weakness: val shows exactly the same scene, people and camera as train "
      "a few seconds earlier, so val scores will be optimistic. It also drops the gap frames, and the end of a clip may not "
      f"contain the rare classes. Near-duplicate clusters crossing train/val under the fallback: {fb_cross}.", "",
      md(FBT.reset_index()), "",
      ("Classes < 20 val boxes under fallback: " + ", ".join(f"{c} ({FBT.loc['val', c]})" for c in weak_val_fb)) if weak_val_fb else "", ""]

L += ["## 8. Statistics (de-duplicated original frames, 15 target classes, proposed split)", "",
      f"Total frames {n_frames}; boxes {n_boxes_t}; background frames {n_bg} ({100 * n_bg / n_frames:.1f}%).", "",
      "### Boxes per class / frames containing class", "",
      md(S["boxes_per_class"].join(S["frames_containing_class"], rsuffix="_frames"), index=True), "",
      f"Class imbalance: largest `{imb['largest']}` {imb['largest_n']} vs smallest `{imb['smallest']}` {imb['smallest_n']} "
      f"→ **{imb['ratio']:.1f} : 1**. Classes with < 100 boxes in train: " + (", ".join(f"{c} ({S['boxes_per_class'].loc[c, 'train']})" for c in lt100_train) or "none") + ".", "",
      "### Paired (yes/no) classes", "", md(S["paired_classes"]), "",
      "### Frames per hazard tag", "",
      "`marked` = in hazards_marked; `violation_box` = a box with status violation for that hazard; `either` = union.", "",
      md(S["frames_per_hazard"]), "",
      "H-02 and H-07 are never tagged: they are interaction hazards (hand_bare + rte_food, raw_meat → rte_food) with no "
      "violation-status box class, so the annotator does not tag them; the rule engine must derive them. "
      "H-04 tags come only from `phone_in_hand` boxes (a non-target class); `phone` boxes are status hard_negative.", "",
      "### Box size at training scale (longest side → 640 px, scale 0.238 for 2688×1520)", "",
      md(S["box_size_at_640"]), "",
      ("**Hard to detect at imgsz 640 (> 25% of boxes under 16 px on the shorter side):** " + ", ".join(hard_small)) if hard_small else "No class has > 25% of boxes under 16 px.", "",
      "### Frames per annotator / camera view / session label / clip / resolution", "",
      md(S["frames_per_annotator"]), "", md(S["frames_per_camera_view"]), "", md(S["frames_per_session_label"]), "",
      md(S["frames_per_clip"]), "", md(S["image_resolutions"]), "",
      "### Boxes per class per annotator", "", md(S["annotator_x_class"], index=True), "",
      "Charts: `audit/charts/boxes_per_class.png`, `boxes_per_class_train_val.png`, `box_size_per_class.png`. "
      "CSVs: `audit/csv/stats_*.csv`.", ""]

L += ["## 9. Visual spot check", "",
      f"{len(chosen)} frames drawn from `bbox_yolo` onto the raw image in `audit/visual_check/` "
      f"(covering every class and all annotators; index in `_index.csv`).", ""]

L += ["## 10. Decisions needed before Phase 2", ""]
qn = 1
if len(other):
    L += [f"{qn}. **Other-class boxes** ({', '.join(f'{k}: {v}' for k, v in other_cnt.items())}): drop these boxes "
          "(the frames are kept), or rename them to a target class (e.g. `phone_in_hand` → `phone`, `mask_incorrect` → `mask_off`)? "
          "Nothing is renamed without your OK."]; qn += 1
L += [f"{qn}. **Split**: accept the clip-based split above ({len(val_groups)} val clips, "
      f"{100 * ST.loc['val', 'frames'] / n_frames:.1f}% val frames), or use the time-based fallback?"]; qn += 1
L += [f"{qn}. **Conflicting labels** ({len(issues['conflicting_labels'])} pairs in {issues['conflicting_labels'].frame_id.nunique()} frames): "
      "keep the frames and list them for the team to fix (default), or exclude those frames? "
      f"**Tiny boxes** ({len(tiny)}): drop (default)?"]; qn += 1
if len(content_dup):
    L += [f"{qn}. **Same image under two frame_ids** ({len(content_dup)}): keep only the latest-edited copy (proposed)?"]
(AUDIT / "AUDIT_REPORT.md").write_text("\n".join(L) + "\n")
print("report written:", AUDIT / "AUDIT_REPORT.md")
print("val groups:", val_groups); print(ST.to_string())
