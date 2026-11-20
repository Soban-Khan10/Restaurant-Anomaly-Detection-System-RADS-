#!/usr/bin/env python3
"""RADS targeted offline augmentation of rare-class TRAIN frames.

Follows RADS_augmentation_instructions.md:
  step 1  choose train frames (raw_meat / smoking / bin_open -> 2 copies; else phone -> 1 copy)
  step 2  augment with the fixed albumentations pipeline (seed 0)
  step 3  write augmented_only/ and dataset_final/
  step 4  verify everything twice (Route A = script records, Route B = re-read files on disk)
  step 5  write AUGMENTATION_REPORT.md and stats_aug/*.csv

dataset/ is read only. The script refuses to run if augmented_only/ or dataset_final/ already exist.

Usage: python augment_rare_classes.py <prepared_dir>
"""
import os
os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")  # no network version check

import sys, json, shutil, hashlib, random, traceback
from pathlib import Path
from collections import Counter, defaultdict

import cv2
import numpy as np
import pandas as pd
import albumentations as A

# ----------------------------------------------------------------------------- paths
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]).resolve()
SRC = ROOT / "dataset"
AUG = ROOT / "augmented_only"
FIN = ROOT / "dataset_final"
VIS = ROOT / "audit" / "aug_visual_check"
STATS = ROOT / "stats_aug"
REPORT = ROOT / "AUGMENTATION_REPORT.md"
SCRIPT = Path(__file__).resolve()
WORK = ROOT / "_work" / "aug"

for p in (AUG, FIN):
    if p.exists():
        sys.exit(f"STOP: {p} already exists. Not overwriting; ask the user.")
for p in (VIS, STATS, REPORT):
    if p.exists():
        sys.exit(f"STOP: {p} already exists. Not overwriting; ask the user.")

SEED = 0
random.seed(SEED)
np.random.seed(SEED)

NAMES = ["person", "head_covered", "head_uncovered", "mask_on", "mask_off", "apron", "no_apron",
         "hand_bare", "hand_gloved", "rte_food", "raw_meat", "smoking", "phone", "bin_closed", "bin_open"]
NC = len(NAMES)
GROUP_A = {10, 11, 14}          # 2 copies
GROUP_B = {12}                  # 1 copy (only if not in group A)
RARE = [10, 11, 12, 14]         # retry if any of these is lost
MAX_RETRIES = 5
PAIRS = [(1, 2), (3, 4), (5, 6), (8, 7), (13, 14)]  # (yes, no)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_labels(p: Path):
    """Return list of (cls:int, cx, cy, w, h) and list of problems."""
    rows, probs = [], []
    txt = p.read_text().strip()
    if not txt:
        return rows, probs
    for i, line in enumerate(txt.splitlines(), 1):
        f = line.split()
        if len(f) != 5:
            probs.append(f"{p.name}:{i}: {len(f)} fields")
            continue
        try:
            c = int(f[0]); v = [float(x) for x in f[1:]]
        except ValueError:
            probs.append(f"{p.name}:{i}: not numeric"); continue
        if str(c) != f[0]:
            probs.append(f"{p.name}:{i}: class id not an integer string '{f[0]}'")
        if not (0 <= c < NC):
            probs.append(f"{p.name}:{i}: class {c} outside 0-14")
        if not all(0.0 <= x <= 1.0 for x in v):
            probs.append(f"{p.name}:{i}: value outside 0-1")
        if not (v[2] > 0 and v[3] > 0):
            probs.append(f"{p.name}:{i}: width/height not > 0")
        rows.append((c, *v))
    return rows, probs


def count_cls(rows):
    return Counter(r[0] for r in rows)


def cstr(counter):
    return ";".join(f"{k}:{counter[k]}" for k in sorted(counter))


def load_yaml_names(p: Path):
    import yaml
    d = yaml.safe_load(p.read_text())
    n = d["names"]
    if isinstance(n, dict):
        return [n[k] for k in sorted(n)]
    return list(n)


def snapshot(d: Path):
    return {str(p.relative_to(d)): (p.stat().st_size, sha256(p)) for p in sorted(d.rglob("*")) if p.is_file()
            and p.name != ".DS_Store"}


# ============================================================================= start
print(f"albumentations {A.__version__}, opencv {cv2.__version__}, numpy {np.__version__}, pandas {pd.__version__}")
print(f"ROOT {ROOT}")
assert load_yaml_names(SRC / "data.yaml") == NAMES, "dataset/data.yaml names differ from the instruction list"

print("Hashing dataset/ (read-only snapshot before) ...")
SNAP_BEFORE = snapshot(SRC)

tr_imgs = sorted((SRC / "images" / "train").glob("*.jpg"))
tr_lbls = sorted((SRC / "labels" / "train").glob("*.txt"))
va_imgs = sorted((SRC / "images" / "val").glob("*.jpg"))
va_lbls = sorted((SRC / "labels" / "val").glob("*.txt"))
assert {p.stem for p in tr_imgs} == {p.stem for p in tr_lbls}, "train images/labels do not pair up"
assert {p.stem for p in va_imgs} == {p.stem for p in va_lbls}, "val images/labels do not pair up"

src_problems = []
train_labels = {}
for p in tr_lbls:
    rows, pr = read_labels(p); src_problems += pr; train_labels[p.stem] = rows
val_labels = {}
for p in va_lbls:
    rows, pr = read_labels(p); src_problems += pr; val_labels[p.stem] = rows
if src_problems:
    print("Problems in original labels (reported, not changed):")
    for x in src_problems[:50]:
        print("  ", x)

# ============================================================================= step 1: plan
plan = []  # (frame_id, group, n_copies, triggers)
for fid in sorted(train_labels):
    cls = set(r[0] for r in train_labels[fid])
    a = sorted(cls & GROUP_A)
    if a:
        plan.append((fid, "A (raw_meat/smoking/bin_open)", 2, [NAMES[c] for c in a]))
    elif cls & GROUP_B:
        plan.append((fid, "B (phone only)", 1, ["phone"]))

nA = sum(1 for x in plan if x[2] == 2)
nB = sum(1 for x in plan if x[2] == 1)
n_planned = sum(x[2] for x in plan)
expected_added = Counter()
for fid, g, n, _ in plan:
    for c, k in count_cls(train_labels[fid]).items():
        expected_added[c] += k * n

print("\n=== STEP 1: PLAN (train only) ===")
print(f"Group A (contains raw_meat/smoking/bin_open) -> 2 copies: {nA} frames -> {2 * nA} images")
print(f"Group B (contains phone, not group A)        -> 1 copy : {nB} frames -> {nB} images")
print(f"Other train frames (no copies): {len(train_labels) - nA - nB}")
print(f"Augmented images planned: {n_planned}")
print("Expected boxes added per class (boxes in chosen frames x copies):")
for c in range(NC):
    print(f"  {c:2d} {NAMES[c]:15s} {expected_added[c]}")
print(f"  total {sum(expected_added.values())}")
sys.stdout.flush()

# ============================================================================= step 2: pipeline (exactly as specified)
T = A.Compose([
    A.HorizontalFlip(p=0.5),
    A.Affine(scale=(0.9, 1.1), translate_percent=(-0.05, 0.05), rotate=(-5, 5),
             border_mode=cv2.BORDER_CONSTANT, p=0.7),
    A.RandomBrightnessContrast(brightness_limit=0.2, contrast_limit=0.2, p=0.6),
    A.HueSaturationValue(hue_shift_limit=5, sat_shift_limit=15, val_shift_limit=10, p=0.4),
    A.RandomGamma(gamma_limit=(85, 115), p=0.3),
    A.GaussNoise(std_range=(0.01, 0.04), p=0.25),
    A.MotionBlur(blur_limit=3, p=0.15),
    A.ImageCompression(quality_range=(60, 90), p=0.3),
], bbox_params=A.BboxParams(format="yolo", label_fields=["class_ids"], min_visibility=0.6, clip=True),
   seed=0, save_applied_params=True)


def short_params(params):
    out = {}
    for k, v in params.items():
        if isinstance(v, (bool, int, str)) or v is None:
            out[k] = v
        elif isinstance(v, float):
            out[k] = round(v, 4)
        elif isinstance(v, (np.integer,)):
            out[k] = int(v)
        elif isinstance(v, (np.floating,)):
            out[k] = round(float(v), 4)
        elif isinstance(v, (tuple, list)) and len(v) <= 4 and all(isinstance(x, (int, float, np.number)) for x in v):
            out[k] = [round(float(x), 4) for x in v]
        elif isinstance(v, dict) and len(v) <= 4 and all(isinstance(x, (int, float, np.number)) for x in v.values()):
            out[k] = {kk: round(float(x), 4) for kk, x in v.items()}
    return out


for d in (AUG / "images", AUG / "labels"):
    d.mkdir(parents=True, exist_ok=False)

aug_records = []
aug_labels = {}       # aug stem -> rows
gen_problems = []
print("\n=== STEP 2: generating ===")
for i, (fid, group, ncopy, triggers) in enumerate(plan, 1):
    img_bgr = cv2.imread(str(SRC / "images" / "train" / f"{fid}.jpg"), cv2.IMREAD_COLOR)
    if img_bgr is None:
        gen_problems.append(f"{fid}: cannot read image"); continue
    H, W = img_bgr.shape[:2]
    img = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    rows = train_labels[fid]
    bxs = [list(r[1:]) for r in rows]
    cls = [r[0] for r in rows]
    src_cnt = count_cls(rows)
    for copy in (1, 2)[:ncopy]:
        retries = 0
        while True:
            out = T(image=img, bboxes=np.array(bxs, dtype=np.float32).reshape(-1, 4), class_ids=cls)
            o_cls = [int(round(float(c))) for c in out["class_ids"]]
            o_cnt = Counter(o_cls)
            lost_rare = [c for c in RARE if o_cnt[c] < src_cnt[c]]
            if not lost_rare or retries >= MAX_RETRIES:
                break
            retries += 1
        stem = f"{fid}_aug{copy}"
        o_img = cv2.cvtColor(out["image"], cv2.COLOR_RGB2BGR)
        if o_img.shape[:2] != (H, W):
            gen_problems.append(f"{stem}: output size {o_img.shape[:2]} != {(H, W)}")
        ok = cv2.imwrite(str(AUG / "images" / f"{stem}.jpg"), o_img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            gen_problems.append(f"{stem}: imwrite failed")
        lines, kept = [], []
        for c, b in zip(o_cls, out["bboxes"]):
            cx, cy, w, h = (float(x) for x in b)
            vals = [round(x, 6) for x in (cx, cy, w, h)]
            if not (vals[2] > 0 and vals[3] > 0) or not all(0 <= v <= 1 for v in vals):
                gen_problems.append(f"{stem}: box class {c} invalid after rounding {vals}; dropped")
                continue
            lines.append(f"{c} {vals[0]:.6f} {vals[1]:.6f} {vals[2]:.6f} {vals[3]:.6f}")
            kept.append((c, *vals))
        (AUG / "labels" / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
        aug_labels[stem] = kept
        k_cnt = count_cls(kept)
        lost_cnt = Counter({c: src_cnt[c] - k_cnt[c] for c in src_cnt if src_cnt[c] - k_cnt[c] > 0})
        lost_rare_final = [NAMES[c] for c in RARE if k_cnt[c] < src_cnt[c]]
        applied = out.get("applied_transforms")
        if applied is not None:
            names_applied = "|".join(n for n, _ in applied)
            params_applied = json.dumps({n: short_params(p) for n, p in applied}, separators=(",", ":"))
        else:
            names_applied, params_applied = "", ""
        aug_records.append({
            "aug_image": f"{stem}.jpg", "aug_label": f"{stem}.txt", "source_frame_id": fid, "copy": copy,
            "group": group, "trigger_classes": ";".join(triggers),
            "src_boxes_total": len(rows), "aug_boxes_total": len(kept), "boxes_lost": len(rows) - len(kept),
            "src_boxes_per_class": cstr(src_cnt), "aug_boxes_per_class": cstr(k_cnt),
            "boxes_lost_per_class": cstr(lost_cnt),
            "retries_used": retries, "rare_box_lost_after_retries": ";".join(lost_rare_final),
            "transforms_applied": names_applied, "transform_params": params_applied,
        })
        if lost_rare_final:
            print(f"  LOG: {stem}: rare box still lost after {retries} retries: {lost_rare_final}")
    if i % 50 == 0 or i == len(plan):
        print(f"  {i}/{len(plan)} frames done, {len(aug_records)} images")
        sys.stdout.flush()

aug_df = pd.DataFrame(aug_records)
aug_df.to_csv(AUG / "aug_index.csv", index=False)
(AUG / "classes.txt").write_text("\n".join(NAMES) + "\n")
if gen_problems:
    print("Generation problems:"); [print("  ", x) for x in gen_problems]

# ============================================================================= step 3: dataset_final
print("\n=== STEP 3: building dataset_final ===")
for sub in ("images/train", "labels/train", "images/val", "labels/val"):
    (FIN / sub).mkdir(parents=True, exist_ok=False)
for p in tr_imgs: shutil.copy2(p, FIN / "images" / "train" / p.name)
for p in tr_lbls: shutil.copy2(p, FIN / "labels" / "train" / p.name)
for p in va_imgs: shutil.copy2(p, FIN / "images" / "val" / p.name)
for p in va_lbls: shutil.copy2(p, FIN / "labels" / "val" / p.name)
for p in sorted((AUG / "images").glob("*.jpg")): shutil.copy2(p, FIN / "images" / "train" / p.name)
for p in sorted((AUG / "labels").glob("*.txt")): shutil.copy2(p, FIN / "labels" / "train" / p.name)
yaml_lines = [f"path: {FIN}", "train: images/train", "val: images/val",
              "# test: added later, when the separately recorded test set is ready", "names:"]
yaml_lines += [f"  {i}: {n}" for i, n in enumerate(NAMES)]
(FIN / "data.yaml").write_text("\n".join(yaml_lines) + "\n")
print("done")

# ============================================================================= step 4: verify
print("\n=== STEP 4: verification ===")
checks = []


def check(name, a, b, ok=None):
    ok = (a == b) if ok is None else ok
    checks.append({"check": name, "route_A": a, "route_B": b, "result": "PASS" if ok else "FAIL"})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: A={a} B={b}")


# --- Route A numbers (script records)
A_orig_train_imgs = len(tr_imgs)
A_orig_val_imgs = len(va_imgs)
A_n_aug = len(aug_records)
A_orig_cls = Counter(); [A_orig_cls.update(count_cls(r)) for r in train_labels.values()]
A_val_cls = Counter(); [A_val_cls.update(count_cls(r)) for r in val_labels.values()]
A_aug_cls = Counter(); [A_aug_cls.update(count_cls(r)) for r in aug_labels.values()]
A_fin_cls = A_orig_cls + A_aug_cls

# --- Route B numbers (re-read disk)
aug_img_files = sorted(p.name for p in (AUG / "images").iterdir() if p.name != ".DS_Store")
aug_lbl_files = sorted(p.name for p in (AUG / "labels").iterdir() if p.name != ".DS_Store")
aug_top = sorted(p.name for p in AUG.iterdir() if p.name != ".DS_Store")
orig_stems = {p.stem for p in tr_imgs} | {p.stem for p in va_imgs}

import re
pat = re.compile(r"_aug[12]$")
all_aug_named = all(pat.search(Path(n).stem) for n in aug_img_files + aug_lbl_files)
check("augmented_only/ top level holds only images, labels, aug_index.csv, classes.txt",
      "images,labels,aug_index.csv,classes.txt", ",".join(sorted(aug_top, key=lambda s: ["images", "labels", "aug_index.csv", "classes.txt"].index(s) if s in ["images", "labels", "aug_index.csv", "classes.txt"] else 99)))
check("augmented_only/: every file name ends in _aug1 or _aug2", True, all_aug_named)
check("augmented_only/: original frames present", 0,
      sum(1 for n in aug_img_files + aug_lbl_files if Path(n).stem in orig_stems))
check("augmented images = planned in step 1", n_planned, len(aug_img_files))
check("augmented images recorded by script = on disk", A_n_aug, len(aug_img_files))
check("augmented label files = augmented images (one each, same names)", len(aug_img_files), len(aug_lbl_files),
      ok={Path(n).stem for n in aug_img_files} == {Path(n).stem for n in aug_lbl_files})
check("aug_index.csv rows = augmented images on disk", A_n_aug, len(pd.read_csv(AUG / "aug_index.csv")))

fin_tr_imgs = sorted(p.name for p in (FIN / "images" / "train").iterdir() if p.name != ".DS_Store")
fin_tr_lbls = sorted(p.name for p in (FIN / "labels" / "train").iterdir() if p.name != ".DS_Store")
fin_va_imgs = sorted(p.name for p in (FIN / "images" / "val").iterdir() if p.name != ".DS_Store")
fin_va_lbls = sorted(p.name for p in (FIN / "labels" / "val").iterdir() if p.name != ".DS_Store")
check("dataset_final images/train = original train + augmented", A_orig_train_imgs + A_n_aug, len(fin_tr_imgs),
      ok=set(fin_tr_imgs) == {p.name for p in tr_imgs} | set(aug_img_files))
check("dataset_final labels/train = original train + augmented", A_orig_train_imgs + A_n_aug, len(fin_tr_lbls),
      ok=set(fin_tr_lbls) == {p.name for p in tr_lbls} | set(aug_lbl_files))
check("dataset_final images/val = original val", A_orig_val_imgs, len(fin_va_imgs),
      ok=set(fin_va_imgs) == {p.name for p in va_imgs})
check("dataset_final labels/val = original val", A_orig_val_imgs, len(fin_va_lbls),
      ok=set(fin_va_lbls) == {p.name for p in va_lbls})
check("dataset_final val: augmented files", 0, sum(1 for n in fin_va_imgs + fin_va_lbls if pat.search(Path(n).stem)))

print("  hashing copies ...")
mism = 0; n_cmp = 0
for sub, files in (("images/train", tr_imgs), ("labels/train", tr_lbls), ("images/val", va_imgs), ("labels/val", va_lbls)):
    for p in files:
        n_cmp += 1
        if SNAP_BEFORE[f"{sub}/{p.name}"][1] != sha256(FIN / sub / p.name):
            mism += 1
check("original train+val files in dataset_final byte-identical to dataset/ (sha256)", n_cmp, n_cmp - mism, ok=mism == 0)
mism = sum(1 for n in aug_img_files if sha256(AUG / "images" / n) != sha256(FIN / "images" / "train" / n))
mism += sum(1 for n in aug_lbl_files if sha256(AUG / "labels" / n) != sha256(FIN / "labels" / "train" / n))
check("augmented files in dataset_final byte-identical to augmented_only/ (sha256)",
      len(aug_img_files) + len(aug_lbl_files), len(aug_img_files) + len(aug_lbl_files) - mism, ok=mism == 0)

# label line checks (Route B: every label file in dataset_final and augmented_only)
bad_lines = []
B = defaultdict(Counter)          # route-B class counts
B_frames = defaultdict(Counter)   # route-B frames containing class
B_boxes_total = Counter()
for key, folder in (("orig_train", SRC / "labels" / "train"), ("aug", AUG / "labels"),
                    ("final_train", FIN / "labels" / "train"), ("final_val", FIN / "labels" / "val"),
                    ("orig_val", SRC / "labels" / "val")):
    for p in sorted(folder.glob("*.txt")):
        rows, pr = read_labels(p)
        if key in ("aug", "final_train", "final_val"):
            bad_lines += [f"{key}/{x}" for x in pr]
        cc = count_cls(rows)
        B[key].update(cc)
        B_frames[key].update(set(cc))
        B_boxes_total[key] += len(rows)
n_lines = B_boxes_total["final_train"] + B_boxes_total["final_val"]
check("label lines: 5 fields, class 0-14, values in 0-1, w,h > 0 (dataset_final, all lines)", n_lines,
      n_lines - len(bad_lines), ok=not bad_lines)
if bad_lines:
    print("   bad lines (first 20):", bad_lines[:20])

# image resolution of augmented images (Route B: decode headers)
from PIL import Image
sizes = Counter()
for n in aug_img_files:
    with Image.open(AUG / "images" / n) as im:
        sizes[im.size] += 1
check("augmented images have 2688x1520 resolution", A_n_aug, sizes.get((2688, 1520), 0))

for c in range(NC):
    check(f"boxes class {c:2d} {NAMES[c]}: original train + augmented = final train",
          f"{A_orig_cls[c]}+{A_aug_cls[c]}={A_fin_cls[c]}",
          f"{B['orig_train'][c]}+{B['aug'][c]}={B['final_train'][c]}",
          ok=(A_orig_cls[c] == B["orig_train"][c] and A_aug_cls[c] == B["aug"][c] and A_fin_cls[c] == B["final_train"][c]
              and B["orig_train"][c] + B["aug"][c] == B["final_train"][c]))
check("boxes all classes: original train + augmented = final train",
      f"{sum(A_orig_cls.values())}+{sum(A_aug_cls.values())}={sum(A_fin_cls.values())}",
      f"{B_boxes_total['orig_train']}+{B_boxes_total['aug']}={B_boxes_total['final_train']}",
      ok=sum(A_fin_cls.values()) == B_boxes_total["final_train"] == B_boxes_total["orig_train"] + B_boxes_total["aug"])
check("val boxes per class unchanged (dataset/ vs dataset_final/)", cstr(A_val_cls), cstr(B["final_val"]),
      ok=A_val_cls == B["final_val"] == B["orig_val"])
check("data.yaml names and order identical to dataset/data.yaml",
      ",".join(load_yaml_names(SRC / "data.yaml")), ",".join(load_yaml_names(FIN / "data.yaml")))
import yaml
fy = yaml.safe_load((FIN / "data.yaml").read_text())
check("dataset_final/data.yaml path = absolute path of dataset_final", str(FIN), fy["path"])
check("no rare-class box lost after retries (count of affected images)", 0,
      int((aug_df["rare_box_lost_after_retries"].fillna("") != "").sum()),
      ok=True)  # informational: logged, kept as instructed
checks[-1]["result"] = "INFO"
check("generation problems", 0, len(gen_problems))

# --- visual check: 20 augmented images spread over the 4 rare classes
VIS.mkdir(parents=True, exist_ok=False)
rng = random.Random(SEED)
chosen, per = [], {}
pool = {c: sorted(s for s, r in aug_labels.items() if any(x[0] == c for x in r)) for c in RARE}
for c in RARE:
    cand = [s for s in pool[c] if s not in chosen]
    pick = rng.sample(cand, min(5, len(cand)))
    chosen += pick; per[c] = pick
while len(chosen) < 20:
    rest = [s for s in aug_labels if s not in chosen]
    if not rest: break
    chosen.append(rng.choice(sorted(rest)))
palette = [(255, 255, 255), (0, 200, 0), (0, 0, 255), (0, 255, 0), (0, 0, 200), (255, 200, 0), (0, 128, 255),
           (180, 180, 180), (255, 0, 255), (255, 255, 0), (0, 0, 128), (0, 255, 255), (255, 0, 0), (128, 128, 0),
           (128, 0, 255)]
vis_rows = []
for s in chosen:
    im = cv2.imread(str(AUG / "images" / f"{s}.jpg"))
    h, w = im.shape[:2]
    rows, _ = read_labels(AUG / "labels" / f"{s}.txt")   # boxes drawn from the label FILE
    for c, cx, cy, bw, bh in rows:
        x1, y1 = int((cx - bw / 2) * w), int((cy - bh / 2) * h)
        x2, y2 = int((cx + bw / 2) * w), int((cy + bh / 2) * h)
        t = 6 if c in RARE else 3
        cv2.rectangle(im, (x1, y1), (x2, y2), palette[c], t)
        cv2.putText(im, NAMES[c], (x1, max(y1 - 8, 30)), cv2.FONT_HERSHEY_SIMPLEX, 1.2, palette[c], 3)
    focus = [NAMES[c] for c in RARE if s in per.get(c, [])]
    cv2.putText(im, f"{s} | focus: {','.join(focus)}", (20, h - 30), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 255, 255), 3)
    out = VIS / f"{'_'.join(focus) or 'extra'}__{s}.jpg"
    cv2.imwrite(str(out), im, [cv2.IMWRITE_JPEG_QUALITY, 90])
    vis_rows.append({"file": out.name, "aug_image": f"{s}.jpg", "focus_class": ";".join(focus),
                     "boxes_drawn": len(rows), "classes": cstr(count_cls(rows))})
pd.DataFrame(vis_rows).to_csv(VIS / "visual_check_index.csv", index=False)
check("visual check images written (spread over 4 rare classes)", 20, len(vis_rows),
      ok=len(vis_rows) == 20 and all(len(per[c]) == 5 for c in RARE))

# --- ultralytics check_det_dataset (if installed)
ultra_result = "ultralytics not installed: skipped"
try:
    import ultralytics
    from ultralytics.data.utils import check_det_dataset
    WORK.mkdir(parents=True, exist_ok=True)
    cwd = os.getcwd(); os.chdir(WORK)
    try:
        d = check_det_dataset(str(FIN / "data.yaml"))
        ultra_result = (f"OK (ultralytics {ultralytics.__version__}): nc={d['nc']}, train={d['train']}, val={d['val']}, "
                        f"names match={[d['names'][i] for i in range(d['nc'])] == NAMES}")
        uok = d["nc"] == NC and [d["names"][i] for i in range(d["nc"])] == NAMES
    finally:
        os.chdir(cwd)
    check("ultralytics check_det_dataset(dataset_final/data.yaml)", "OK, nc=15",
          f"OK, nc={d['nc']}" if uok else ultra_result, ok=uok)
except ImportError:
    pass
except Exception as e:
    ultra_result = f"FAILED: {type(e).__name__}: {e}"
    check("ultralytics check_det_dataset(dataset_final/data.yaml)", "OK", ultra_result, ok=False)
# ultralytics must not leave cache files in our output
for p in list(FIN.rglob("*.cache")):
    p.unlink()

print("  hashing dataset/ (read-only snapshot after) ...")
SNAP_AFTER = snapshot(SRC)
check("dataset/ unchanged (files, sizes, sha256 before vs after)", len(SNAP_BEFORE), len(SNAP_AFTER),
      ok=SNAP_BEFORE == SNAP_AFTER)

n_fail = sum(1 for c in checks if c["result"] == "FAIL")
print(f"\nVERIFICATION: {sum(1 for c in checks if c['result'] == 'PASS')} PASS, {n_fail} FAIL")

# ============================================================================= step 5: stats + report
STATS.mkdir(parents=True, exist_ok=False)
pd.DataFrame(checks).to_csv(STATS / "08_verification.csv", index=False)
if n_fail:
    print("STOP: verification failed; report not written. See stats_aug/08_verification.csv")
    sys.exit(1)

pd.DataFrame([{"frame_id": f, "group": g, "copies": n, "trigger_classes": ";".join(t)} for f, g, n, t in plan]) \
    .to_csv(STATS / "00_plan_frames.csv", index=False)

n_aug_boxes = sum(A_aug_cls.values())
n_lost = int(aug_df["boxes_lost"].sum())
n_retry_imgs = int((aug_df["retries_used"] > 0).sum())
n_retry_total = int(aug_df["retries_used"].sum())
lost_after = aug_df[aug_df["rare_box_lost_after_retries"].fillna("") != ""]
lost_per_class = Counter()
for s in aug_df["boxes_lost_per_class"].fillna(""):
    for kv in str(s).split(";"):
        if kv:
            k, v = kv.split(":"); lost_per_class[int(k)] += int(v)

s5a = pd.DataFrame([
    {"item": "augmented images", "value": A_n_aug},
    {"item": "  from group A frames (2 copies each)", "value": 2 * nA},
    {"item": "  from group B frames (1 copy each)", "value": nB},
    {"item": "source frames used", "value": len(plan)},
    {"item": "boxes in augmented images", "value": n_aug_boxes},
    {"item": "boxes expected if nothing was lost", "value": sum(expected_added.values())},
    {"item": "boxes lost to the edge (min_visibility 0.6)", "value": n_lost},
    {"item": "images that needed at least one retry", "value": n_retry_imgs},
    {"item": "retries used in total", "value": n_retry_total},
    {"item": "images where a rare box was still lost after 5 retries", "value": len(lost_after)},
])
s5a.to_csv(STATS / "05a_augmented_summary.csv", index=False)

s5b = pd.DataFrame([{"id": c, "class": NAMES[c], "expected_boxes": expected_added[c], "boxes_in_augmented": A_aug_cls[c],
                     "boxes_lost": lost_per_class[c],
                     "augmented_images_containing": B_frames["aug"][c]} for c in range(NC)])
s5b.loc[len(s5b)] = {"id": "", "class": "total", "expected_boxes": sum(expected_added.values()),
                     "boxes_in_augmented": n_aug_boxes, "boxes_lost": n_lost, "augmented_images_containing": ""}
s5b.to_csv(STATS / "05b_boxes_per_class_augmented.csv", index=False)

s5c = []
for c in RARE:
    src_frames = [f for f, g, n, t in plan if any(r[0] == c for r in train_labels[f])]
    tot_frames = sum(1 for r in train_labels.values() if any(x[0] == c for x in r))
    s5c.append({"id": c, "class": NAMES[c], "train_frames_with_class": tot_frames,
                "frames_that_received_copies": len(src_frames),
                "copies_made_from_them": sum(n for f, g, n, t in plan if f in set(src_frames)),
                "augmented_images_still_containing_class": B_frames["aug"][c]})
s5c = pd.DataFrame(s5c); s5c.to_csv(STATS / "05c_frames_per_rare_class.csv", index=False)

fin_imgs = A_orig_train_imgs + A_n_aug
s6a = pd.DataFrame([
    {"set": "original train", "frames": A_orig_train_imgs, "boxes": B_boxes_total["orig_train"]},
    {"set": "augmented (added)", "frames": A_n_aug, "boxes": B_boxes_total["aug"]},
    {"set": "final train", "frames": len(fin_tr_imgs), "boxes": B_boxes_total["final_train"]},
    {"set": "val (unchanged)", "frames": len(fin_va_imgs), "boxes": B_boxes_total["final_val"]},
])
s6a.to_csv(STATS / "06a_frames_and_boxes.csv", index=False)

tot_fin = B_boxes_total["final_train"]
tot_orig = B_boxes_total["orig_train"]
s6b = pd.DataFrame([{"id": c, "class": NAMES[c], "original_train": B["orig_train"][c], "added": B["aug"][c],
                     "final_train": B["final_train"][c], "val": B["final_val"][c],
                     "original_train_share_%": round(100 * B["orig_train"][c] / tot_orig, 2),
                     "final_train_share_%": round(100 * B["final_train"][c] / tot_fin, 2)} for c in range(NC)])
s6b.to_csv(STATS / "06b_boxes_per_class.csv", index=False)

s6c = pd.DataFrame([{"id": c, "class": NAMES[c], "original_train": B_frames["orig_train"][c], "added": B_frames["aug"][c],
                     "final_train": B_frames["final_train"][c]} for c in range(NC)])
s6c.to_csv(STATS / "06c_frames_containing_class.csv", index=False)


def pair_row(cnt, y, n):
    a, b = cnt[y], cnt[n]
    big, small = max(a, b), min(a, b)
    return a, b, round(100 * a / (a + b), 1), round(100 * b / (a + b), 1), round(big / small, 2)


s6d = []
for y, n in PAIRS:
    ba = pair_row(B["orig_train"], y, n); af = pair_row(B["final_train"], y, n)
    s6d.append({"pair (yes / no)": f"{NAMES[y]} / {NAMES[n]}",
                "before_yes": ba[0], "before_no": ba[1], "before_yes_%": ba[2], "before_no_%": ba[3],
                "before_ratio (larger:smaller)": ba[4],
                "after_yes": af[0], "after_no": af[1], "after_yes_%": af[2], "after_no_%": af[3],
                "after_ratio (larger:smaller)": af[4]})
s6d = pd.DataFrame(s6d); s6d.to_csv(STATS / "06d_paired_classes.csv", index=False)


def imb(cnt):
    mx = max(range(NC), key=lambda c: cnt[c]); mn = min(range(NC), key=lambda c: cnt[c])
    return mx, cnt[mx], mn, cnt[mn], round(cnt[mx] / cnt[mn], 2)


ib, ia = imb(B["orig_train"]), imb(B["final_train"])
s6e = pd.DataFrame([
    {"when": "before (original train)", "largest_class": NAMES[ib[0]], "largest_boxes": ib[1],
     "smallest_class": NAMES[ib[2]], "smallest_boxes": ib[3], "imbalance_ratio": ib[4]},
    {"when": "after (final train)", "largest_class": NAMES[ia[0]], "largest_boxes": ia[1],
     "smallest_class": NAMES[ia[2]], "smallest_boxes": ia[3], "imbalance_ratio": ia[4]},
])
s6e.to_csv(STATS / "06e_imbalance_ratio.csv", index=False)
share_aug = round(100 * A_n_aug / fin_imgs, 2)
s6f = pd.DataFrame([{"original_train_images": A_orig_train_imgs, "augmented_images": A_n_aug,
                     "final_train_images": fin_imgs, "augmented_share_%": share_aug}])
s6f.to_csv(STATS / "06f_augmented_share.csv", index=False)

# per-transform usage (how often each was applied)
tf_use = Counter()
for s in aug_df["transforms_applied"].fillna(""):
    for n in str(s).split("|"):
        if n: tf_use[n] += 1
pd.DataFrame([{"transform": k, "images_applied": v, "share_%": round(100 * v / A_n_aug, 1)} for k, v in tf_use.items()]) \
    .to_csv(STATS / "07_transform_usage.csv", index=False)


# ----------------------------------------------------------------------------- markdown
def md(df):
    cols = list(df.columns)
    out = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(f"{v:,}" if isinstance(v, (int, np.integer)) and not isinstance(v, bool) else str(v)
                                      for v in r.values) + " |")
    return "\n".join(out)


rel = lambda p: str(p.relative_to(ROOT.parent))
TF = [
    ("HorizontalFlip", "mirror left-right", 0.5),
    ("Affine", "scale 0.9–1.1, shift ±5 % of width/height, rotate ±5°, empty border filled black", 0.7),
    ("RandomBrightnessContrast", "brightness ±0.2, contrast ±0.2", 0.6),
    ("HueSaturationValue", "hue ±5, saturation ±15, value ±10", 0.4),
    ("RandomGamma", "gamma 85–115 (0.85–1.15)", 0.3),
    ("GaussNoise", "noise std 1–4 % of the pixel range", 0.25),
    ("MotionBlur", "kernel 3 px (lightest possible)", 0.15),
    ("ImageCompression", "JPEG quality 60–90", 0.3),
]
tf_table = pd.DataFrame([{"transform": n, "range": r, "probability": p,
                          "actually applied to (images)": f"{tf_use.get(n, 0):,} ({round(100 * tf_use.get(n, 0) / A_n_aug, 1)} %)"}
                         for n, r, p in TF])

lost_list = ""
if len(lost_after):
    lost_list = "\n".join(f"  - `{r.aug_image}`: lost {r.rare_box_lost_after_retries} (source {r.src_boxes_per_class}, "
                          f"augmented {r.aug_boxes_per_class})" for r in lost_after.itertuples())
else:
    lost_list = "  - none"

ck = pd.DataFrame(checks)[["check", "route_A", "route_B", "result"]]
ck_md = md(ck.astype(str))
n_pass = int((ck["result"] == "PASS").sum()); n_info = int((ck["result"] == "INFO").sum())

rep = f"""# RADS augmentation report

Generated by `scripts/augment_rare_classes.py` (albumentations {A.__version__}, OpenCV {cv2.__version__}, random seed 0).
No training was started.

## 1. Summary

Extra, altered copies were made of the **training** frames that contain the rare classes raw_meat, smoking, bin_open
(2 copies each) and phone (1 copy), to reduce class imbalance. This produced **{A_n_aug:,} augmented images** with
**{n_aug_boxes:,} boxes** from {len(plan):,} source frames, so the final training set has {fin_imgs:,} images
({A_orig_train_imgs:,} original + {A_n_aug:,} augmented). Augmentation was applied to the training split only; the
validation split (and the separately recorded test set, which is not part of this dataset) is never augmented and was
copied unchanged. YOLO's own online augmentation stays switched on during training; this step only adds targeted
copies for the rare classes.

## 2. Why

Before augmentation the training split was strongly imbalanced: the largest class, {NAMES[ib[0]]}, has
{ib[1]:,} boxes and the smallest, {NAMES[ib[2]]}, has {ib[3]:,} boxes (ratio **{ib[4]} : 1**). Copying every
frame would multiply all classes by the same factor and keep the same imbalance, so only frames that contain the
rare classes were copied. Note that the copied frames also contain common classes (people, hands, food, ...), so
those classes grow too; the rare classes grow by a much larger percentage.

## 3. Which frames and how many copies

Rule (train frames only, read from the label files):

- a frame containing **raw_meat (10), smoking (11) or bin_open (14)** gets **2 copies** (group A);
- otherwise, a frame containing **phone (12)** gets **1 copy** (group B);
- all other train frames get no copies.

| group | frames | copies per frame | augmented images |
|---|---|---|---|
| A: raw_meat / smoking / bin_open | {nA:,} | 2 | {2 * nA:,} |
| B: phone (not in group A) | {nB:,} | 1 | {nB:,} |
| no copies | {A_orig_train_imgs - nA - nB:,} | 0 | 0 |
| **total** | {A_orig_train_imgs:,} | | **{A_n_aug:,}** |

The list of chosen frames is in `stats_aug/00_plan_frames.csv`.

## 4. Transformations

Every copy is made from the original full-resolution image (2688×1520) and saved at the same resolution
(JPEG quality 95). Each transform is applied independently with the probability shown, so each copy gets a
different random mix.

{md(tf_table)}

Boxes are transformed together with the image. A box is kept only if at least 60 % of it is still visible
(`min_visibility=0.6`); boxes partly outside the image are clipped to the image edge. If a rare-class box
(raw_meat, smoking, phone, bin_open) of the original was lost, the copy was redrawn with new random values, up to
5 times. Note: with rotation, albumentations (default `rotate_method="largest_box"`) uses the box that encloses the
rotated box, so boxes in rotated copies can be very slightly larger (at most ±5°).

**Deliberately not used**, and why:

| not used | reason |
|---|---|
| downscaling / lower resolution | objects are already very small at 640 px (smoking ≈ 7 px); smaller would make them disappear |
| zoom-out below 0.9 | same reason: shrinks small objects further |
| large rotation | the CCTV cameras are fixed and level; large tilts never happen in real footage |
| vertical flip | upside-down kitchens never happen with fixed, level cameras |
| cutout / random erasing | could erase the tiny rare objects (cigarette, phone) while their label stays |
| heavy blur | would erase the fine detail that identifies small objects |
| mixup | blends two scenes; unrealistic for fixed cameras and confuses small objects |

## 5. How much was augmented (augmented data only)

{md(s5a)}

Images where a rare-class box was still lost after 5 retries (kept as instructed, logged):
{lost_list}

**Boxes per class in the augmented data**

{md(s5b)}

**Frames per rare class that received copies**

{md(s5c)}

(A frame can contain more than one rare class, so these rows overlap.)

## 6. Final training data (original + augmented)

**Frames and boxes**

{md(s6a)}

**Boxes per class**

{md(s6b)}

**Frames containing each class**

{md(s6c)}

**Paired yes/no classes (train), before and after**

{md(s6d)}

**Imbalance ratio (largest class / smallest class, train boxes)**

{md(s6e)}

**Share of augmented images in final train:** {A_n_aug:,} of {fin_imgs:,} images = **{share_aug} %**.

## 7. Note for the project report

The official dataset statistics remain those in `DATA_RECORD.md` (original data only). Augmented counts are reported
separately, e.g. "train: {A_orig_train_imgs:,} original + {A_n_aug:,} augmented frames ({B_boxes_total['orig_train']:,}
original + {B_boxes_total['aug']:,} augmented boxes); val: {len(fin_va_imgs):,} frames, not augmented".
Augmented copies are derived from training frames only, so they add no new scenes, people or sessions; val scores
remain optimistic for the reasons given in `DATA_RECORD.md`.

## 8. Verification

Route A = counts from the script's own records; Route B = counts from re-reading the files on disk.
Result: **{n_pass} PASS, {n_fail} FAIL**{f", {n_info} INFO (logged, not a failure)" if n_info else ""}.

{ck_md}

ultralytics `check_det_dataset`: {ultra_result}

## 9. Files

| what | where |
|---|---|
| augmented data only | `{rel(AUG)}/` (images/, labels/, aug_index.csv, classes.txt) |
| index of every augmented image | `{rel(AUG / 'aug_index.csv')}` |
| training-ready dataset | `{rel(FIN)}/` (data.yaml path = `{FIN}`) |
| script | `{rel(SCRIPT)}` |
| visual check (20 images, boxes drawn from label files) | `{rel(VIS)}/` |
| tables of sections 5 and 6, verification | `{rel(STATS)}/` |
| original dataset (unchanged) | `{rel(SRC)}/` |
"""
REPORT.write_text(rep)
print(f"\nReport written: {REPORT}")
print(json.dumps({"augmented_images": A_n_aug, "boxes_added": {NAMES[c]: B["aug"][c] for c in range(NC)},
                  "final_train": {NAMES[c]: B["final_train"][c] for c in range(NC)},
                  "imbalance_before": ib[4], "imbalance_after": ia[4], "pass": n_pass, "fail": n_fail}, indent=1))
