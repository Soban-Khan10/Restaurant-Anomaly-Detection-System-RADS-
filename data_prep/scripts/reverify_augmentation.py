#!/usr/bin/env python3
"""Independent re-verification of the RADS augmentation (read only; shares no code with augment_rare_classes.py).

Writes only: audit/aug_reverify/ (CSV results).
Usage: python reverify_augmentation.py <prepared_dir> [n_frames_to_regenerate]
"""
import os
os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")
import sys, re, csv, hashlib
from pathlib import Path
from collections import Counter, defaultdict
import numpy as np
import cv2
import yaml

R = Path(sys.argv[1]).resolve()
NREGEN = int(sys.argv[2]) if len(sys.argv) > 2 else 40
SRC, AUG, FIN = R / "dataset", R / "augmented_only", R / "dataset_final"
OUT = R / "audit" / "aug_reverify"
OUT.mkdir(parents=True, exist_ok=True)
NAMES = ["person", "head_covered", "head_uncovered", "mask_on", "mask_off", "apron", "no_apron", "hand_bare",
         "hand_gloved", "rte_food", "raw_meat", "smoking", "phone", "bin_closed", "bin_open"]
RARE = (10, 11, 12, 14)
results = []


def rec(name, expected, found, ok=None, note=""):
    ok = (expected == found) if ok is None else ok
    results.append([name, expected, found, "PASS" if ok else "FAIL", note])
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: expected={expected} found={found} {note}")


def md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


def labels(p):
    out = []
    for ln in p.read_text().splitlines():
        if ln.strip():
            out.append(ln.split())
    return out


def valid(fields):
    if len(fields) != 5: return False
    try:
        c = int(fields[0]); v = list(map(float, fields[1:]))
    except ValueError:
        return False
    return 0 <= c <= 14 and all(0 <= x <= 1 for x in v) and v[2] > 0 and v[3] > 0


def listdir(d):
    return sorted(x.name for x in d.iterdir())


# ---------------------------------------------------------------- 1. splits from index.csv
idx = list(csv.DictReader(open(SRC / "index.csv")))
split_of = {r["frame_id"]: r["split"] for r in idx}
tr = {f[:-4] for f in listdir(SRC / "images" / "train")}
va = {f[:-4] for f in listdir(SRC / "images" / "val")}
rec("index.csv train frames = dataset/images/train", len(tr), sum(1 for s in split_of.values() if s == "train"),
    ok={k for k, s in split_of.items() if s == "train"} == tr)
rec("index.csv val frames = dataset/images/val", len(va), sum(1 for s in split_of.values() if s == "val"),
    ok={k for k, s in split_of.items() if s == "val"} == va)

# ---------------------------------------------------------------- 2. re-derive the plan independently
src_lab = {f: labels(SRC / "labels" / "train" / f"{f}.txt") for f in tr}
plan = {}
for f, L in src_lab.items():
    cs = {int(x[0]) for x in L}
    if cs & {10, 11, 14}: plan[f] = 2
    elif 12 in cs: plan[f] = 1
expected_aug = {f"{f}_aug{k}" for f, n in plan.items() for k in range(1, n + 1)}

# ---------------------------------------------------------------- 3. augmented_only contents
ai = listdir(AUG / "images"); al = listdir(AUG / "labels")
rec("augmented_only top-level entries", "['aug_index.csv', 'classes.txt', 'images', 'labels']", str(listdir(AUG)))
rec("augmented_only: no hidden/extra files", 0,
    sum(1 for x in ai + al if x.startswith(".") or not re.fullmatch(r".+_aug[12]\.(jpg|txt)", x)))
rec("augmented image set = independently re-derived plan", len(expected_aug), len(ai),
    ok={x[:-4] for x in ai} == expected_aug)
rec("augmented label set = augmented image set", len(ai), len(al), ok={x[:-4] for x in ai} == {x[:-4] for x in al})
rec("augmented sources that are val frames (leakage)", 0,
    sum(1 for x in ai if re.sub(r"_aug[12]$", "", x[:-4]) in va))
rec("augmented sources not found in train", 0,
    sum(1 for x in ai if re.sub(r"_aug[12]$", "", x[:-4]) not in tr))
rec("augmented names colliding with an original frame name", 0, sum(1 for x in ai if x[:-4] in tr | va))
rec("classes.txt = 15 names in id order", NAMES, (AUG / "classes.txt").read_text().split())

# ---------------------------------------------------------------- 4. per augmented image: labels vs source, image
rows = []
bad_lines = 0; rare_lost = 0; cls_added = Counter(); extra_cls = 0; more_than_src = 0
area_ratios = defaultdict(list); flagged_area = []
pix = []
for x in ai:
    s = x[:-4]; f = re.sub(r"_aug[12]$", "", s)
    L = labels(AUG / "labels" / f"{s}.txt"); S = src_lab[f]
    bad_lines += sum(1 for l in L if not valid(l))
    ca = Counter(int(l[0]) for l in L); cs = Counter(int(l[0]) for l in S)
    cls_added.update(ca)
    extra_cls += sum(1 for c in ca if c not in cs)
    more_than_src += sum(1 for c in ca if ca[c] > cs[c])
    rare_lost += sum(1 for c in RARE if ca[c] < cs[c])
    # box sizes: same class, same count -> compare sorted areas (pixels^2 ratio)
    for c in ca:
        if ca[c] == cs[c]:
            a1 = sorted(float(l[3]) * float(l[4]) for l in L if int(l[0]) == c)
            a0 = sorted(float(l[3]) * float(l[4]) for l in S if int(l[0]) == c)
            for p, q in zip(a1, a0):
                r = p / q; area_ratios[c].append(r)
                if not (0.45 <= r <= 1.6):
                    flagged_area.append((s, NAMES[c], round(r, 3)))
    im = cv2.imread(str(AUG / "images" / x)); im0 = cv2.imread(str(SRC / "images" / "train" / f"{f}.jpg"))
    shape_ok = im is not None and im.shape == im0.shape == (1520, 2688, 3)
    mad = float(np.abs(im.astype(np.int16) - im0.astype(np.int16)).mean()) if shape_ok else -1
    pix.append((s, mad, shape_ok))
    rows.append([s, f, len(S), len(L), mad, shape_ok])

rec("augmented label lines valid (5 fields, class 0-14, 0-1, w,h>0)", 0, bad_lines)
rec("augmented labels: classes not present in source frame", 0, extra_cls)
rec("augmented labels: class has more boxes than in source", 0, more_than_src)
rec("rare-class boxes lost (vs source frame)", 0, rare_lost)
rec("augmented images decodable, 2688x1520x3, same size as source", len(ai), sum(1 for p in pix if p[2]))
rec("box area ratio aug/source within 0.45-1.6 (scale 0.9-1.1, rotation, clipping)", 0, len(flagged_area),
    note=str(flagged_area[:10]))
near_dupes = [p for p in pix if 0 <= p[1] < 1.0]
results.append(["augmented images nearly identical to source (mean abs pixel diff < 1.0)", "",
                len(near_dupes), "INFO", ";".join(f"{a}:{b:.2f}" for a, b, _ in near_dupes)])
print(f"[INFO] near-identical to source: {len(near_dupes)} {[(a, round(b, 2)) for a, b, _ in near_dupes]}")
# aug1 vs aug2 of the same frame must differ
same12 = 0
for f, n in plan.items():
    if n == 2 and md5(AUG / "images" / f"{f}_aug1.jpg") == md5(AUG / "images" / f"{f}_aug2.jpg"):
        same12 += 1
rec("aug1 and aug2 of the same frame byte-identical", 0, same12)
with open(OUT / "per_image.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["aug", "source", "src_boxes", "aug_boxes", "mean_abs_pixel_diff", "shape_ok"])
    w.writerows(rows)

# ---------------------------------------------------------------- 5. aug_index.csv vs disk
with open(AUG / "aug_index.csv") as fh:
    ix = list(csv.DictReader(fh))
mis = 0
for r in ix:
    L = labels(AUG / "labels" / r["aug_label"])
    c = Counter(int(l[0]) for l in L)
    if int(r["aug_boxes_total"]) != len(L) or r["aug_boxes_per_class"] != ";".join(f"{k}:{c[k]}" for k in sorted(c)):
        mis += 1
rec("aug_index.csv box counts = label files on disk", len(ix), len(ix) - mis, ok=mis == 0 and len(ix) == len(ai))

# ---------------------------------------------------------------- 6. dataset_final
ft_i = listdir(FIN / "images" / "train"); ft_l = listdir(FIN / "labels" / "train")
fv_i = listdir(FIN / "images" / "val"); fv_l = listdir(FIN / "labels" / "val")
rec("dataset_final top level", "['data.yaml', 'images', 'labels']", str(listdir(FIN)))
rec("dataset_final images/train = original train + augmented", len(tr) + len(ai), len(ft_i),
    ok=set(ft_i) == {f"{x}.jpg" for x in tr} | set(ai))
rec("dataset_final labels/train = original train + augmented", len(tr) + len(al), len(ft_l),
    ok=set(ft_l) == {f"{x}.txt" for x in tr} | set(al))
rec("dataset_final val = original val (images, labels)", 2 * len(va), len(fv_i) + len(fv_l),
    ok=set(fv_i) == {f"{x}.jpg" for x in va} and set(fv_l) == {f"{x}.txt" for x in va})
rec("augmented files inside dataset_final val", 0, sum(1 for x in fv_i + fv_l if "_aug" in x))
diff = 0; n = 0
for sub, names in (("images/train", [f"{x}.jpg" for x in tr]), ("labels/train", [f"{x}.txt" for x in tr]),
                   ("images/val", fv_i), ("labels/val", fv_l)):
    for nm in names:
        n += 1; diff += md5(SRC / sub / nm) != md5(FIN / sub / nm)
rec("original files in dataset_final identical to dataset/ (md5)", n, n - diff, ok=diff == 0)
diff = sum(md5(AUG / "images" / x) != md5(FIN / "images" / "train" / x) for x in ai) + \
       sum(md5(AUG / "labels" / x) != md5(FIN / "labels" / "train" / x) for x in al)
rec("augmented files in dataset_final identical to augmented_only (md5)", 2 * len(ai), 2 * len(ai) - diff, ok=diff == 0)
cnt = lambda d: Counter(int(l[0]) for p in d.glob("*.txt") for l in labels(p))
c_src, c_fin, c_aug = cnt(SRC / "labels" / "train"), cnt(FIN / "labels" / "train"), cnt(AUG / "labels")
rec("per-class boxes: original train + augmented = final train (15 classes)", 15,
    sum(1 for c in range(15) if c_src[c] + c_aug[c] == c_fin[c]))
rec("final val boxes per class = original val", str(sorted(cnt(SRC / "labels" / "val").items())),
    str(sorted(cnt(FIN / "labels" / "val").items())))
y0 = yaml.safe_load((SRC / "data.yaml").read_text()); y1 = yaml.safe_load((FIN / "data.yaml").read_text())
rec("data.yaml names identical", y0["names"], y1["names"])
rec("data.yaml path / train / val", f"{FIN} images/train images/val", f"{y1['path']} {y1['train']} {y1['val']}")
rec("data.yaml has no test entry", False, "test" in y1)
rec("ultralytics .cache files in dataset_final", 0, len(list(FIN.rglob("*.cache"))))
# near-duplicate guard: no augmented image byte-identical to any original
orig_md5 = {md5(SRC / "images" / "train" / f"{x}.jpg") for x in tr} | {md5(SRC / "images" / "val" / f"{x}.jpg") for x in va}
rec("augmented images byte-identical to an original image", 0, sum(md5(AUG / "images" / x) in orig_md5 for x in ai))

# ---------------------------------------------------------------- 7. reproducibility: regenerate first N plan frames
import albumentations as A
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
lab_same = img_same = tf_names = 0; total = 0; first_applied = []
ix_by = {r["aug_image"][:-4]: r for r in ix}
for f in sorted(plan)[:NREGEN]:
    img = cv2.cvtColor(cv2.imread(str(SRC / "images" / "train" / f"{f}.jpg")), cv2.COLOR_BGR2RGB)
    S = src_lab[f]
    b = np.array([[float(v) for v in l[1:]] for l in S], dtype=np.float32).reshape(-1, 4)
    c = [int(l[0]) for l in S]; cs = Counter(c)
    for k in range(1, plan[f] + 1):
        for t in range(6):
            o = T(image=img, bboxes=b, class_ids=c)
            oc = Counter(int(round(float(z))) for z in o["class_ids"])
            if all(oc[r] >= cs[r] for r in RARE): break
        lines = [f"{int(round(float(z)))} " + " ".join(f"{v:.6f}" for v in (round(float(q), 6) for q in bb))
                 for z, bb in zip(o["class_ids"], o["bboxes"])]
        s = f"{f}_aug{k}"
        total += 1
        lab_same += "\n".join(lines) == "\n".join(" ".join(l) for l in labels(AUG / "labels" / f"{s}.txt"))
        ok, enc = cv2.imencode(".jpg", cv2.cvtColor(o["image"], cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
        img_same += hashlib.md5(enc.tobytes()).hexdigest() == md5(AUG / "images" / f"{s}.jpg")
        names = "|".join(n for n, _ in o.get("applied_transforms", []))
        tf_names += names == (ix_by[s]["transforms_applied"] or "")
        if len(first_applied) < 4:
            first_applied.append((s, names))
rec(f"reproducibility: labels regenerated with seed 0 identical (first {NREGEN} plan frames)", total, lab_same)
rec(f"reproducibility: images regenerated with seed 0 byte-identical", total, img_same)
rec(f"reproducibility: applied-transform record identical", total, tf_names, note=str(first_applied))

with open(OUT / "reverify_results.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["check", "expected", "found", "result", "note"]); w.writerows(results)
with open(OUT / "box_area_ratio_per_class.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["class", "n_boxes", "min", "p5", "median", "p95", "max"])
    for c in sorted(area_ratios):
        a = np.array(area_ratios[c])
        w.writerow([NAMES[c], len(a)] + [round(float(v), 3) for v in (a.min(), *np.percentile(a, [5, 50, 95]), a.max())])
nf = sum(r[3] == "FAIL" for r in results)
print(f"\nRE-VERIFICATION: {sum(r[3] == 'PASS' for r in results)} PASS, {nf} FAIL, "
      f"{sum(r[3] == 'INFO' for r in results)} INFO")
