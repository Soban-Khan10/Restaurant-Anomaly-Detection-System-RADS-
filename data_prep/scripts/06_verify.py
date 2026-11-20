#!/usr/bin/env python3
"""Phase 2 verification. Route A = _work/expected_counts.json (from the cleaned in-memory table, 05).
Route B = re-read everything from disk: dataset/ images (sizes from the files), label .txt files, data.yaml,
index.csv, and for the renumbering check the ORIGINAL annotations.json files (and source ZIPs for byte checks).
Does not import clean_lib. Every check is PASS/FAIL with both values.
Also redraws 15 random frames from the written label files into dataset_check/.

Usage: python 06_verify.py <prepared_dir>
"""
import sys, json, re, zipfile, hashlib, random
from pathlib import Path
from collections import Counter, defaultdict
import numpy as np
import pandas as pd
import yaml
from PIL import Image, ImageDraw, ImageFont
import imagehash

OUT = Path(sys.argv[1]).resolve()
DS = OUT / "dataset"
EXP = json.load(open(OUT / "_work" / "expected_counts.json"))
NAMES = ["person", "head_covered", "head_uncovered", "mask_on", "mask_off", "apron", "no_apron",
         "hand_bare", "hand_gloved", "rte_food", "raw_meat", "smoking", "phone", "bin_closed", "bin_open"]
RENAME = {"phone_in_hand": "phone", "mask_incorrect": "mask_off"}  # user decision 2026-11-20
results = []


def check(name, a, b, ok=None):
    ok = (a == b) if ok is None else ok
    results.append({"check": name, "route_A_in_memory": json.dumps(a, default=str) if not isinstance(a, (int, str)) else a,
                    "route_B_from_disk": json.dumps(b, default=str) if not isinstance(b, (int, str)) else b,
                    "result": "PASS" if ok else "FAIL"})
    return ok


# ---------------------------------------------------------------- read files from disk
IMG_EXT = {".jpg", ".jpeg", ".png"}
disk = {}
for s in ("train", "val"):
    imgs = {p.stem: p for p in (DS / "images" / s).iterdir() if p.suffix.lower() in IMG_EXT}
    labs = {p.stem: p for p in (DS / "labels" / s).iterdir() if p.suffix == ".txt"}
    disk[s] = (imgs, labs)

frames = []   # route B frame table
boxes = []    # route B box table
bad_lines = []
for s, (imgs, labs) in disk.items():
    for fid, p in imgs.items():
        with Image.open(p) as im:
            w, h = im.size
        lines = labs[fid].read_text().splitlines() if fid in labs else []
        n = 0
        for ln in lines:
            if not ln.strip():
                continue
            parts = ln.split()
            ok = len(parts) == 5 and re.fullmatch(r"\d+", parts[0]) and all(re.fullmatch(r"\d\.\d{6}", x) for x in parts[1:])
            if not ok:
                bad_lines.append((fid, ln)); continue
            cid = int(parts[0]); cx, cy, bw, bh = map(float, parts[1:])
            if not (0 <= cid <= 14) or bw <= 0 or bh <= 0 or cx - bw / 2 < -1e-6 or cy - bh / 2 < -1e-6 \
                    or cx + bw / 2 > 1 + 1e-6 or cy + bh / 2 > 1 + 1e-6:
                bad_lines.append((fid, ln))
            boxes.append({"frame_id": fid, "split": s, "cid": cid, "cx": cx, "cy": cy, "bw": bw, "bh": bh, "W": w, "H": h})
            n += 1
        m = re.search(r"_(\d{17})", fid)
        frames.append({"frame_id": fid, "split": s, "W": w, "H": h, "n": n, "clip": m.group(1) if m else None, "path": str(p)})
FB = pd.DataFrame(frames); BB = pd.DataFrame(boxes)
IDX = pd.read_csv(DS / "index.csv", dtype={"clip": str})
FB = FB.merge(IDX[["frame_id", "annotator", "camera", "split"]].rename(columns={"split": "index_split"}), on="frame_id", how="left")

# ---------------------------------------------------------------- structure checks
for s, (imgs, labs) in disk.items():
    check(f"{s}: image files == label files (count)", len(imgs), len(labs))
    check(f"{s}: every image has exactly one label and vice versa", 0,
          len(set(imgs) ^ set(labs)))
check("label lines well-formed (5 fields, id 0-14, 6 decimals, inside image, w/h > 0): bad lines", 0, len(bad_lines))
check("all class ids between 0 and 14", True, bool(BB.cid.between(0, 14).all()))
y = yaml.safe_load(open(DS / "data.yaml"))
check("data.yaml names == fixed id list (order and spelling)", {i: c for i, c in enumerate(NAMES)}, y["names"])
check("data.yaml train/val paths", ["images/train", "images/val", str(DS)], [y["train"], y["val"], y["path"]])
check("data.yaml has no test entry yet", False, "test" in y)
check("index.csv split matches folder", 0, int((FB.split != FB.index_split).sum()))
check("index.csv rows == image files", len(FB), len(IDX))
check("every frame_id appears exactly once (across train+val)", len(EXP["frame_ids"]), int(FB.frame_id.nunique()),
      ok=FB.frame_id.is_unique and sorted(FB.frame_id) == EXP["frame_ids"])

# ---------------------------------------------------------------- counts, route A vs route B
def stats_b(f, b):
    d = {"frames": len(f), "boxes": len(b), "background_frames": int((f.n == 0).sum()),
         "boxes_per_class": {c: int((b.cid == i).sum()) for i, c in enumerate(NAMES)},
         "frames_with_class": {c: int(b[b.cid == i].frame_id.nunique()) for i, c in enumerate(NAMES)},
         "frames_per_camera": f.camera.value_counts().sort_index().to_dict(),
         "frames_per_clip": f["clip"].value_counts().sort_index().to_dict(),
         "frames_per_recording_date": f["clip"].str[:8].map(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}").value_counts().sort_index().to_dict(),
         "frames_per_annotator": f.annotator.value_counts().sort_index().to_dict(),
         "resolutions": (f.W.astype(str) + "x" + f.H.astype(str)).value_counts().to_dict()}
    present = {c: set(b[b.cid == i].frame_id) for i, c in enumerate(NAMES)}
    d["hazard_frames_tagged"] = {"H-01": len(present["head_uncovered"]), "H-03": len(present["smoking"]),
                                 "H-05": len(present["bin_open"]), "H-06": len(present["no_apron"]),
                                 "H-08": len(present["mask_off"])}
    d["rule_input_frames"] = {"H-02": len(present["hand_bare"] & present["rte_food"]), "H-04": len(present["phone"]),
                              "H-07": len(present["raw_meat"] & (present["hand_bare"] | present["hand_gloved"]))}
    sc = 640 / np.maximum(b.W, b.H)
    w, h = b.bw * b.W * sc, b.bh * b.H * sc
    sh = np.minimum(w, h)
    d["box_size_640"] = {c: {"median_w": round(float(w[b.cid == i].median()), 1),
                             "median_h": round(float(h[b.cid == i].median()), 1),
                             "p10_w": round(float(w[b.cid == i].quantile(.1)), 1),
                             "p10_h": round(float(h[b.cid == i].quantile(.1)), 1),
                             "pct_short_lt16": round(float(100 * (sh[b.cid == i] < 16).mean()), 1),
                             "pct_short_lt8": round(float(100 * (sh[b.cid == i] < 8).mean()), 1)}
                         for i, c in enumerate(NAMES) if (b.cid == i).any()}
    return d


B_STATS = {"overall": stats_b(FB, BB)}
for s in ("train", "val"):
    B_STATS[s] = stats_b(FB[FB.split == s], BB[BB.split == s])

for part in ("overall", "train", "val"):
    a, b = EXP[part], B_STATS[part]
    for k in ("frames", "boxes", "background_frames"):
        check(f"{part}: {k}", a[k], b[k])
    for c in NAMES:
        check(f"{part}: boxes of {c} (id {NAMES.index(c)})", a["boxes_per_class"][c], b["boxes_per_class"][c])
    for c in NAMES:
        check(f"{part}: frames containing {c}", a["frames_with_class"][c], b["frames_with_class"][c])
    for k in ("frames_per_camera", "frames_per_clip", "frames_per_recording_date", "frames_per_annotator", "resolutions"):
        check(f"{part}: {k}", {str(x): v for x, v in a[k].items()}, {str(x): v for x, v in b[k].items()})
    check(f"{part}: hazard frames (A: annotator tags, B: violation classes in label files)", a["hazard_frames_tagged"], b["hazard_frames_tagged"])
    check(f"{part}: rule-engine input frames H-02/H-04/H-07", a["rule_input_frames"], b["rule_input_frames"])
    check(f"{part}: box size at 640 per class", a["box_size_640"], b["box_size_640"])

ov = B_STATS["overall"]
check("train frames + val frames == total frames", ov["frames"], B_STATS["train"]["frames"] + B_STATS["val"]["frames"])
check("train boxes + val boxes == total boxes", ov["boxes"], B_STATS["train"]["boxes"] + B_STATS["val"]["boxes"])
check("sum of boxes per class == total boxes", ov["boxes"], sum(ov["boxes_per_class"].values()))
check("sum of label lines == sum of index.csv num_boxes", int(IDX.num_boxes.sum()), len(BB))

# ---------------------------------------------------------------- groups
clips_tr, clips_va = set(FB[FB.split == "train"]["clip"]), set(FB[FB.split == "val"]["clip"])
check("no video clip in both train and val", 0, len(clips_tr & clips_va))
check("val clips == approved list", EXP["clips"]["val"], sorted(clips_va))
check("every frame_id contains a clip id", 0, int(FB["clip"].isna().sum()))
nd = pd.read_csv(OUT / "audit" / "csv" / "near_duplicate_clusters.csv", dtype={"clip_ts": str})
nd = nd.merge(FB[["frame_id", "split"]], on="frame_id")
check("no near-duplicate cluster (phash <= 4, audit) in both train and val", 0, int((nd.groupby("nd_cluster").split.nunique() > 1).sum()))

# ---------------------------------------------------------------- images are byte copies of the source
mism, zcache = [], {}
for r in IDX.itertuples():
    data = (DS / r.image_path).read_bytes()
    src = r.source_raw_image
    if "::" in src:
        zp, member = src.split("::", 1)
        if zp not in zcache:
            zcache[zp] = zipfile.ZipFile(zp)
        sdata = zcache[zp].read(member)
    else:
        sdata = Path(src).read_bytes()
    if hashlib.sha1(data).digest() != hashlib.sha1(sdata).digest():
        mism.append(r.frame_id)
check("every dataset image is a byte-identical copy of the raw image in the source export/ZIP", 0, len(mism))

# ---------------------------------------------------------------- renumbering check from the ORIGINAL annotations.json
src_name_counts = Counter()
ann_cache = {}
exports = pd.read_csv(OUT / "_work" / "exports.csv")
root_of = dict(zip(exports.export, exports.path))
for r in IDX.itertuples():
    root = root_of[r.source_export]
    if root not in ann_cache:
        a = json.load(open(Path(root) / "annotations.json"))
        ann_cache[root] = {f["frame_id"]: f for f in a["frames"]}
    for b in ann_cache[root][r.frame_id]["boxes"]:
        src_name_counts[b["class_name"]] += 1
removed = Counter()
for t in EXP["tiny_boxes_removed"]:
    removed[t["class_name"]] += 1
for d in EXP["exact_duplicate_boxes_removed"]:
    removed[d["removed_source_class"]] += 1
renum_rows = []
for i, c in enumerate(NAMES):
    renamed_in = sum(src_name_counts[k] - removed[k] for k, v in RENAME.items() if v == c)
    expected = src_name_counts[c] - removed[c] + renamed_in
    got = ov["boxes_per_class"][c]
    renum_rows.append({"class": c, "new_id": i, "source_boxes_with_this_name": src_name_counts[c],
                       "removed_tiny_or_duplicate": removed[c], "renamed_into_this_class": renamed_in,
                       "expected_label_lines": expected, "label_lines_with_new_id": got,
                       "result": "PASS" if expected == got else "FAIL"})
    check(f"renumbering: {c} -> id {i} (source name count - removed + renamed in == label lines)", expected, got)
leftover = {k: v for k, v in src_name_counts.items() if k not in NAMES and k not in RENAME}
check("no source class name left unmapped", {}, leftover)
pd.DataFrame(renum_rows).to_csv(OUT / "_work" / "renumbering_check.csv", index=False)

# ---------------------------------------------------------------- cross-clip visual similarity (information)
ph = {r.frame_id: imagehash.phash(Image.open(r.path)) for r in FB.itertuples()}
trh = np.array([np.array(ph[f].hash).flatten() for f in FB[FB.split == "train"].frame_id])
vah = np.array([np.array(ph[f].hash).flatten() for f in FB[FB.split == "val"].frame_id])
dmin = np.array([(trh != v).sum(1).min() for v in vah])
info = {"val_frames_with_a_train_frame_within_phash_4": int((dmin <= 4).sum()), "val_frames": len(vah),
        "median_min_distance": float(np.median(dmin))}

# ---------------------------------------------------------------- dataset_check: redraw 15 frames from label files
CHK = OUT / "dataset_check"; CHK.mkdir(exist_ok=True)
for p in CHK.glob("*.jpg"):
    p.unlink()
rng = random.Random(2026)
pick = rng.sample(sorted(FB[FB.split == "train"].frame_id), 10) + rng.sample(sorted(FB[FB.split == "val"].frame_id), 5)
try:
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
except Exception:
    font = ImageFont.load_default()
cols = [(230, 230, 230), (76, 195, 138), (255, 92, 92), (63, 182, 232), (255, 122, 69), (143, 209, 79), (245, 69, 92),
        (255, 197, 61), (54, 207, 201), (179, 127, 235), (247, 89, 171), (235, 47, 150), (155, 169, 217), (115, 209, 61), (250, 140, 22)]
for fid in pick:
    r = FB[FB.frame_id == fid].iloc[0]
    im = Image.open(r.path).convert("RGB"); d = ImageDraw.Draw(im)
    lab = DS / "labels" / r.split / f"{fid}.txt"
    for ln in lab.read_text().splitlines():
        cid, cx, cy, bw, bh = ln.split(); cid = int(cid); cx, cy, bw, bh = map(float, (cx, cy, bw, bh))
        x1, y1, x2, y2 = (cx - bw / 2) * r.W, (cy - bh / 2) * r.H, (cx + bw / 2) * r.W, (cy + bh / 2) * r.H
        d.rectangle([x1, y1, x2, y2], outline=cols[cid], width=4)
        t = f"{cid} {NAMES[cid]}"
        d.rectangle([x1, max(0, y1 - 26), x1 + 12 * len(t) + 8, max(0, y1 - 26) + 26], fill=cols[cid])
        d.text((x1 + 4, max(0, y1 - 25)), t, fill=(0, 0, 0), font=font)
    d.rectangle([0, 0, r.W, 34], fill=(0, 0, 0))
    d.text((8, 5), f"{fid} | {r.split} | drawn from dataset/labels (new ids 0-14)", fill=(255, 255, 255), font=font)
    im.resize((r.W // 2, r.H // 2)).save(CHK / f"{r.split}_{fid}.jpg", quality=85)

R = pd.DataFrame(results)
R.to_csv(OUT / "_work" / "verification.csv", index=False)
json.dump({"route_B": B_STATS, "info": info, "n_pass": int((R.result == "PASS").sum()), "n_fail": int((R.result == "FAIL").sum()),
           "dataset_check": pick}, open(OUT / "_work" / "verification_routeB.json", "w"), indent=1, default=str)
print(f"checks: {len(R)}  PASS {int((R.result == 'PASS').sum())}  FAIL {int((R.result == 'FAIL').sum())}")
print(R[R.result == "FAIL"].to_string())
print(info)
