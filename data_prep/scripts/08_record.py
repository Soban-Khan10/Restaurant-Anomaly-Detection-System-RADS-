#!/usr/bin/env python3
"""Phase 2 step 6: write DATA_RECORD.md and stats/ (CSV per table + charts).
All numbers come from _work/expected_counts.json (route A) which 06_verify.py has checked against the files
on disk (route B); the record refuses to build if any verification check failed.

Usage: python 08_record.py <prepared_dir>
"""
import sys, json, datetime
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(sys.argv[1]).resolve()
DS = OUT / "dataset"
ST = OUT / "stats"; ST.mkdir(exist_ok=True)
E = json.load(open(OUT / "_work" / "expected_counts.json"))
V = pd.read_csv(OUT / "_work" / "verification.csv")
VB = json.load(open(OUT / "_work" / "verification_routeB.json"))
SMK = json.load(open(OUT / "_work" / "smoke_result.json")) if (OUT / "_work" / "smoke_result.json").exists() else {}
REN = pd.read_csv(OUT / "_work" / "renumbering_check.csv")
IDX = pd.read_csv(DS / "index.csv", dtype={"clip": str})
EXPORTS = pd.read_csv(OUT / "_work" / "exports.csv")
ZIPS = pd.read_csv(OUT / "audit" / "inventory_zips.csv")
DUPS = pd.read_csv(OUT / "audit" / "csv" / "duplicates_by_frame_id.csv")
import re
_log = (OUT / "_work" / "smoke_log.txt").read_text(errors="ignore") if (OUT / "_work" / "smoke_log.txt").exists() else ""
SCAN = {}
for m in re.finditer(r"(train|val): \S*Scanning \S+\.\.\. (\d+) images, (\d+) backgrounds, (\d+) corrupt: 100%", _log):
    SCAN[m.group(1)] = m.groups()
SCAN = list(SCAN.values())
assert (V.result == "PASS").all(), "verification has failures - fix before writing the record"

CLASSES = ["person", "head_covered", "head_uncovered", "mask_on", "mask_off", "apron", "no_apron",
           "hand_bare", "hand_gloved", "rte_food", "raw_meat", "smoking", "phone", "bin_closed", "bin_open"]
TOOL_ID = {"person": 0, "head_covered": 1, "head_uncovered": 2, "mask_on": 3, "mask_off": 4, "apron": 6, "no_apron": 7,
           "hand_bare": 9, "hand_gloved": 10, "rte_food": 11, "raw_meat": 12, "smoking": 14, "phone": 16,
           "bin_closed": 17, "bin_open": 18}
PAIRS = [("head_covered", "head_uncovered"), ("mask_on", "mask_off"), ("apron", "no_apron"),
         ("hand_gloved", "hand_bare"), ("bin_closed", "bin_open")]
SP = ["overall", "train", "val"]
O, T, VA = E["overall"], E["train"], E["val"]


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "n/a"


def md(df):
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        out.append("| " + " | ".join("" if (isinstance(v, float) and np.isnan(v)) else str(v) for v in row) + " |")
    return "\n".join(out)


tables = {}
tables["01_frames_boxes_background"] = pd.DataFrame(
    [{"measure": m, **{s: E[s][k] for s in SP}} for m, k in
     [("frames", "frames"), ("boxes", "boxes"), ("background frames (no boxes)", "background_frames")]])
tables["01_frames_boxes_background"].loc[3] = ["share of frames", "100.0%", pct(T["frames"], O["frames"]), pct(VA["frames"], O["frames"])]
tables["02_boxes_per_class"] = pd.DataFrame([{"id": i, "class": c, **{s: E[s]["boxes_per_class"][c] for s in SP},
                                              "val share": pct(VA["boxes_per_class"][c], O["boxes_per_class"][c])}
                                             for i, c in enumerate(CLASSES)])
tables["03_frames_containing_class"] = pd.DataFrame([{"id": i, "class": c, **{s: E[s]["frames_with_class"][c] for s in SP}}
                                                     for i, c in enumerate(CLASSES)])
pr = []
for a, b in PAIRS:
    row = {"pair (yes / no)": f"{a} / {b}"}
    for s in SP:
        na, nb = E[s]["boxes_per_class"][a], E[s]["boxes_per_class"][b]
        row[s] = f"{a} {na} ({pct(na, na + nb)}) · {b} {nb} ({pct(nb, na + nb)}) · ratio {max(na, nb) / min(na, nb):.1f} : 1"
    pr.append(row)
tables["04_paired_classes"] = pd.DataFrame(pr)
HZN = {"H-01": "Missing hair restraint / head cover", "H-02": "Bare-hand contact with RTE food",
       "H-03": "Smoking / vaping in prep area", "H-04": "Phone use during food handling",
       "H-05": "Open / overflowing waste bin", "H-06": "Missing / soiled apron or uniform",
       "H-07": "No hand hygiene: raw meat -> RTE", "H-08": "Missing required face mask"}
HZSRC = {"H-01": "head_uncovered box (annotator tag)", "H-03": "smoking box (annotator tag)",
         "H-05": "bin_open box (annotator tag)", "H-06": "no_apron box (annotator tag)", "H-08": "mask_off box (annotator tag)",
         "H-02": "DERIVED by rule engine; frames with hand_bare + rte_food", "H-04": "DERIVED by rule engine; frames with a phone box",
         "H-07": "DERIVED by rule engine; frames with raw_meat + a hand box (needs the time sequence to decide)"}
hz = []
for h in sorted(HZN):
    key = "hazard_frames_tagged" if h in E["overall"]["hazard_frames_tagged"] else "rule_input_frames"
    hz.append({"hazard": h, "name": HZN[h], "source": HZSRC[h], **{s: E[s][key][h] for s in SP}})
tables["05_frames_per_hazard"] = pd.DataFrame(hz)


def dist(key, label):
    keys = sorted(set().union(*[E[s][key].keys() for s in SP]))
    return pd.DataFrame([{label: k, **{s: E[s][key].get(k, 0) for s in SP}} for k in keys])


tables["06_frames_per_camera"] = dist("frames_per_camera", "camera")
tables["07_frames_per_recording_date"] = dist("frames_per_recording_date", "recording date")
clip_meta = IDX.groupby("clip").agg(recording_date=("recording_date", "first"), camera=("camera", "first"),
                                    split=("split", "first"), frames=("frame_id", "count"),
                                    annotators=("annotator", lambda x: ", ".join(sorted(set(x))))).reset_index()
clip_meta["recording start"] = pd.to_datetime(clip_meta["clip"].str[:14], format="%Y%m%d%H%M%S").dt.strftime("%H:%M:%S")
cb = pd.read_csv(OUT / "_work" / "boxes_final.csv", dtype={"clip": str}).groupby("clip").size().rename("boxes")
clip_meta = clip_meta.merge(cb, on="clip", how="left").fillna({"boxes": 0})
clip_meta["boxes"] = clip_meta["boxes"].astype(int)
tables["08_frames_per_clip"] = clip_meta[["clip", "recording_date", "recording start", "camera", "split", "frames", "boxes", "annotators"]]
tables["09_frames_per_annotator"] = dist("frames_per_annotator", "annotator")
bs = []
for i, c in enumerate(CLASSES):
    o = O["box_size_640"][c]
    bs.append({"id": i, "class": c, "boxes": O["boxes_per_class"][c], "median w": o["median_w"], "median h": o["median_h"],
               "p10 w": o["p10_w"], "p10 h": o["p10_h"], "% short side < 16 px (overall)": o["pct_short_lt16"],
               "% < 16 px (train)": T["box_size_640"][c]["pct_short_lt16"], "% < 16 px (val)": VA["box_size_640"][c]["pct_short_lt16"],
               "% short side < 8 px (overall)": o["pct_short_lt8"],
               "hard at 640?": "YES" if o["pct_short_lt16"] > 25 else ""})
tables["10_box_size_at_640"] = pd.DataFrame(bs)
tables["11_resolutions"] = dist("resolutions", "resolution")
tables["12_renumbering_check"] = REN
tables["13_verification"] = V
for k, df in tables.items():
    df.to_csv(ST / f"{k}.csv", index=False)

# charts
bpc = tables["02_boxes_per_class"].set_index("class")
plt.figure(figsize=(10, 5)); bpc["overall"].plot.bar(color="#4C72B0"); plt.ylabel("boxes")
plt.title("Boxes per class (original data, no augmentation)"); plt.tight_layout(); plt.savefig(ST / "chart_boxes_per_class.png", dpi=130); plt.close()
ax = bpc[["train", "val"]].plot.bar(figsize=(11, 5), color=["#4C72B0", "#DD8452"]); ax.set_yscale("log")
plt.ylabel("boxes (log scale)"); plt.title("Boxes per class: train vs val"); plt.tight_layout()
plt.savefig(ST / "chart_boxes_per_class_train_val.png", dpi=130); plt.close()
B = pd.read_csv(OUT / "_work" / "boxes_final.csv")
sc = 640 / np.maximum(B.meta_width, B.meta_height)
B["short640"] = np.minimum(B.bw * B.meta_width * sc, B.bh * B.meta_height * sc)
plt.figure(figsize=(12, 5))
plt.boxplot([B[B.class_name == c].short640 for c in CLASSES], tick_labels=CLASSES, showfliers=False)
plt.axhline(16, color="r", ls="--", lw=1, label="16 px"); plt.axhline(8, color="orange", ls="--", lw=1, label="8 px")
plt.yscale("log"); plt.xticks(rotation=45, ha="right"); plt.ylabel("shorter box side at 640 px (log)"); plt.legend()
plt.title("Box size per class at training scale (image longest side = 640 px)"); plt.tight_layout()
plt.savefig(ST / "chart_box_size_per_class.png", dpi=130); plt.close()

# ------------------------------------------------------------------ text
ex = EXPORTS.copy()
zmap = {Path(r.extracted_to).name: Path(r.zip).relative_to(Path(r.zip).parents[1] if "Maham" in r.zip or "Tahreem" in r.zip else Path(r.zip).parent).as_posix()
        for r in ZIPS.itertuples()}
ex["file"] = ex.export.map(lambda e: zmap.get(e, e + "/ (unzipped folder)"))
used = IDX.groupby("source_export").size()
ex["frames used"] = ex.export.map(used).fillna(0).astype(int)
ex_tab = ex[["file", "exported_by", "exported_at", "frames_in_annotations_json", "frames used"]].rename(
    columns={"exported_by": "exported by", "exported_at": "exported at (UTC)", "frames_in_annotations_json": "frames in export"})
log = pd.DataFrame(E["cleaning_log"])
L = {r.step: r for r in log.itertuples()}
cd = pd.DataFrame(E["content_duplicates_removed"])
xd = pd.DataFrame(E["exact_duplicate_boxes_removed"])
tb = pd.DataFrame(E["tiny_boxes_removed"])
nfr, nbx = O["frames"], O["boxes"]
imb = max(O["boxes_per_class"].values()) / min(O["boxes_per_class"].values())
big = max(O["boxes_per_class"], key=O["boxes_per_class"].get); small = min(O["boxes_per_class"], key=O["boxes_per_class"].get)
hard = [r["class"] for r in bs if r["% short side < 16 px (overall)"] > 25]
lt100 = [c for c in CLASSES if T["boxes_per_class"][c] < 100]
info = VB["info"]
cam2 = O["frames_per_camera"].get("camera 2", 0)
today = datetime.date.today().isoformat()
train_cmd = ("yolo detect train data=<path>/RADS_YOLO_prepared/dataset/data.yaml model=yolo26s.pt "
             "imgsz=640 epochs=100 batch=4 amp=False device=0 workers=2")

R = []
R += ["# RADS training dataset: data record", "",
      "This record describes how the RADS restaurant-CCTV training data was checked, cleaned and split. "
      "Every number below was computed twice: once from the cleaned data table, and once by re-reading the files written "
      "to `dataset/`. All pairs matched (section 8). Tables are also in `stats/` as CSV files.", ""]
R += ["## 1. Summary", "",
      f"- **Date prepared:** {today}",
      "- **What it is:** labelled frames from restaurant CCTV video, for detecting food-safety hazards "
      "(head cover, mask, apron, gloves, food, smoking, phone, bins). Original frames only, no augmentation.",
      "- **Model:** Ultralytics YOLO26s, image size 640, FP32, trained on a GTX 1650 laptop.",
      f"- **Size:** {nfr} frames and {nbx} boxes, in 15 classes.",
      f"- **Split:** train {T['frames']} frames ({pct(T['frames'], nfr)}) and {T['boxes']} boxes; "
      f"val {VA['frames']} frames ({pct(VA['frames'], nfr)}) and {VA['boxes']} boxes. "
      "There is no test split: the test set will be recorded separately later.",
      f"- **Location:** `RADS_YOLO_prepared/dataset/` (`images/`, `labels/`, `data.yaml`, `index.csv`).", ""]
R += ["## 2. Source data", "",
      f"All exports from the RADS Annotator tool found in `All_Yolo_Data` were used: {len(ex)} exports "
      f"({len(ZIPS)} ZIP files plus 1 unzipped folder), all in the same export format (schema version 2). "
      "The source folder was only read, never changed.", "", md(ex_tab), "",
      "**Annotators and frames in the final dataset:** " +
      ", ".join(f"{k} {v}" for k, v in O["frames_per_annotator"].items()) + ".", "",
      "**Restaurant:** one restaurant, `R01`. 98 frames carried the restaurant ID `R01cutte-t`. That is a typing error "
      "for R01 (same camera, same kitchen), so it was corrected to R01.", "",
      "**Cameras:** camera view A = **camera 1** (high diagonal view), camera view B = **camera 2**. "
      + ", ".join(f"{k}: {v} frames" for k, v in O["frames_per_camera"].items()) + ".", "",
      f"**Recordings:** {len(clip_meta)} video clips, recorded on {len(O['frames_per_recording_date'])} evenings: "
      + ", ".join(f"{k} ({v} frames)" for k, v in O["frames_per_recording_date"].items()) +
      ". The recording date and start time come from each clip's file name (for example `20261109220937414` = 2026-11-09 22:09:37). "
      "The `recording_date` field in the annotation files holds the date the clip was added to the tool, not the recording date, so it was not used.", "",
      "**Grouping:** frames are grouped by **video clip and recording date**. The annotators' `session_id` was not used: "
      "each annotator numbered sessions their own way, and many frames kept a default `S01`, so it does not identify a recording.", "",
      md(tables["08_frames_per_clip"]), "",
      "**Image resolution:** " + ", ".join(f"{k} ({v} frames)" for k, v in O["resolutions"].items()) + ".", ""]
R += ["## 3. Cleaning", "",
      "Changes made, with counts and reasons:", "",
      md(pd.DataFrame([
          {"change": "Duplicate frame_ids removed (same frame in two exports, or listed twice)", "count": f"{len(DUPS)} frame copies",
           "reason": "Keep one copy per frame: the copy with the latest annotation edit time (`updated_at`). "
                     + "; ".join((f"`{r.frame_id}`: listed twice in `{r.kept_export}` with identical boxes, one copy kept"
                                  if r.kept_export == r.dropped_export else
                                  f"`{r.frame_id}`: in two exports with different boxes ({r.kept_boxes} vs {r.dropped_boxes}); "
                                  f"kept {r.kept_annotator}'s later edit, removed {r.dropped_annotator}'s older copy")
                                 for r in DUPS.itertuples())},
          {"change": "Same image saved under two frame_ids: older copy removed", "count": f"{len(cd)} frames ({L['same image under two frame_ids: older copy removed'].detail})",
           "reason": "Same picture labelled twice (re-labelled under another session number, or a frozen video frame). Kept the latest-edited copy: "
                     + "; ".join(f"removed `{r.dropped_frame_id}`, kept `{r.kept_frame_id}`" for r in cd.itertuples())},
          {"change": "Class renamed: phone_in_hand → phone", "count": f"{L['renamed phone_in_hand -> phone'].count} boxes",
           "reason": "Team decision: phone_in_hand is not one of the 15 classes; it is a phone. Renamed by name."},
          {"change": "Class renamed: mask_incorrect → mask_off", "count": f"{L['renamed mask_incorrect -> mask_off'].count} boxes",
           "reason": "Team decision: an incorrectly worn mask counts as a mask violation. Renamed by name."},
          {"change": "Tiny boxes dropped (< 4 px wide or high in the original image)", "count": f"{len(tb)} boxes",
           "reason": "Probably accidental clicks: " + "; ".join(f"{r.class_name} {r.w_px}×{r.h_px} px in `{r.frame_id}`" for r in tb.itertuples())},
          {"change": "Exact duplicate boxes removed (same class, IoU > 0.9)", "count": f"{len(xd)} boxes",
           "reason": "The same object drawn twice: " + "; ".join(f"{r.class_name} in `{r.frame_id}`" for r in xd.itertuples())},
          {"change": "Boxes clipped to the image", "count": f"{L['boxes clipped to the image'].count} boxes",
           "reason": L["boxes clipped to the image"].detail},
          {"change": "Restaurant ID R01cutte-t → R01", "count": f"{L['restaurant_id R01cutte-t -> R01'].count} frames", "reason": "Typing error."},
          {"change": "Class numbers rewritten (renumbering)", "count": f"all {nbx} boxes",
           "reason": "The annotation tool numbers classes with gaps (0–22). Every box was given its new id 0–14 by class name (section 4)."},
      ])), "",
      "**Not changed:**",
      "- No frames were excluded apart from the duplicates above. All 9 background frames (no boxes) were kept with empty label files.",
      "- No box was moved, resized or re-labelled, apart from the renames above. No labels were invented.",
      "- Images are exact byte copies of the raw frames (checked by file hash). They were not resized or re-encoded, and no augmentation was applied.",
      "- The annotators' own `split` tags (`train` / `unassigned`) were ignored.",
      "- Label-quality findings from the audit were reported but **not** fixed: 73 of 2,478 person boxes (2.9%) have no head box, "
      "and 163 head/mask/hand/apron boxes lie outside any person box. See `audit/AUDIT_REPORT.md`.", ""]
R += ["## 4. Classes", "",
      "The 15 classes and their final ids. **These ids are fixed.** The trained model and the RADS rule engine both use them, "
      "so they must never be reordered or renumbered. The annotation tool ids are shown for reference only.", "",
      md(pd.DataFrame([{"final id": i, "class": c, "id in RADS Annotator": TOOL_ID[c],
                        "also includes": {"phone": "phone_in_hand (tool id 15), renamed", "mask_off": "mask_incorrect (tool id 5), renamed"}.get(c, "")}
                       for i, c in enumerate(CLASSES)])), ""]
val_clips = clip_meta[clip_meta.split == "val"]
R += ["## 5. How the data was split and why", "",
      "**Method: by whole video clip.** Each of the 20 video clips went entirely to train or entirely to val. A clip is never split.", "",
      "**Why not a random split of frames:** frames next to each other in a video are almost identical. With a random split, "
      "near-copies of the same moment would sit in both train and val. Val scores would then look much better than the model really is (leakage).", "",
      "**How the val clips were chosen:** every possible set of clips was tried (1,048,574 sets). These rules had to hold:",
      "- val is 12–18% of the frames;",
      "- train keeps at least 75% of the boxes of every class;",
      "- at least one camera-2 clip is in val.", "",
      "502 sets met all the rules. Among them, the chosen set (approved by the team) gives the most classes at least 10 val boxes "
      "(all 15 reach it), then the most val boxes for the weakest classes.", "",
      f"**Target and actual:** target 85% train / 15% val. Actual: {pct(T['frames'], nfr)} / {pct(VA['frames'], nfr)} of frames, "
      f"{pct(T['boxes'], nbx)} / {pct(VA['boxes'], nbx)} of boxes.", "",
      f"**Val clips ({len(val_clips)}):**", "", md(val_clips[["clip", "recording_date", "recording start", "camera", "frames", "boxes"]]), "",
      "**Test set:** not part of this dataset. It will be recorded separately, in different sessions, and used only for the final evaluation. "
      "`audit/train_hashes.csv` holds an image hash of every training frame, so the test set can be checked for near-copies of training frames.", ""]
R += ["## 6. Statistics (original data, no augmentation)", "",
      "### Frames, boxes, background", "", md(tables["01_frames_boxes_background"]), "",
      "### Boxes per class", "", md(tables["02_boxes_per_class"]), "",
      f"Class imbalance: largest class `{big}` {O['boxes_per_class'][big]} boxes vs smallest `{small}` {O['boxes_per_class'][small]} boxes, "
      f"ratio {imb:.1f} : 1. Classes with fewer than 100 train boxes: " + (", ".join(f"{c} ({T['boxes_per_class'][c]})" for c in lt100) or "none") + ".", "",
      "### Frames containing each class", "", md(tables["03_frames_containing_class"]), "",
      "### Paired yes/no classes", "", md(tables["04_paired_classes"]), "",
      "### Frames per hazard", "",
      "H-01, H-03, H-05, H-06 and H-08 each have their own violation class, so the count is the frames where that class was labelled. "
      "These counts equal the annotators' hazard tags exactly (checked). "
      "**H-02, H-04 and H-07 are derived by the RADS rule engine, not taken from annotator tags.** "
      "They have no violation class of their own: the rule engine decides them from combinations of classes (and, for H-07, the order of events in time). "
      "For these three the table shows only how many frames contain the input classes the rule engine needs. "
      "That is not the number of violations.", "",
      md(tables["05_frames_per_hazard"]), "",
      "### Frames per camera", "", md(tables["06_frames_per_camera"]), "",
      "### Frames per recording date", "", md(tables["07_frames_per_recording_date"]), "",
      "### Frames per annotator", "", md(tables["09_frames_per_annotator"]), "",
      "(Frames per clip: see section 2.)", "",
      "### Box size at training scale (640 px)", "",
      "Each image is resized so its longest side is 640 px (2688×1520 → 640×362, scale 0.238). "
      "Sizes are in pixels at that scale. A box whose shorter side is under 16 px is hard for the detector to find.", "",
      md(tables["10_box_size_at_640"]), "",
      "Charts: `stats/chart_boxes_per_class.png`, `stats/chart_boxes_per_class_train_val.png`, `stats/chart_box_size_per_class.png`.", ""]
R += ["## 7. Known limitations", "",
      "These should be stated honestly in the project report.", "",
      "1. **One restaurant, one kitchen.** All frames come from restaurant R01. The model has not seen other kitchens, layouts or lighting.",
      f"2. **Two evenings only.** All clips were recorded on {', '.join(O['frames_per_recording_date'])}. "
      f"Camera 2 has only {cam2} frames ({pct(cam2, nfr)}).",
      "3. **Same people and scene in train and val.** The same small group of staff/actors appears in all clips, in the same kitchen, with the same fixed cameras. "
      f"Train and val use different clips, but the scenes still look very alike: {info['val_frames_with_a_train_frame_within_phash_4']} of "
      f"{info['val_frames']} val frames have a train frame from another clip that is nearly identical by image hash (whole-frame phash, distance ≤ 4). "
      "Val scores will therefore be **optimistic**. Only the separately recorded test set (new sessions) measures real performance.",
      f"4. **Small objects.** At imgsz 640, {len(hard)} classes have more than 25% of boxes under 16 px on the shorter side: "
      + ", ".join(f"{c} ({O['box_size_640'][c]['pct_short_lt16']}%)" for c in hard) +
      f". `smoking` is the hardest: median box about {O['box_size_640']['smoking']['median_w']}×{O['box_size_640']['smoking']['median_h']} px, "
      f"and {O['box_size_640']['smoking']['pct_short_lt8']}% of boxes are under 8 px. Expect low recall for these classes at 640. "
      "A larger image size or tiled inference would help but needs more GPU memory than the GTX 1650 has.",
      f"5. **Class imbalance.** `{big}` has {imb:.1f}× more boxes than `{small}`. "
      f"`raw_meat` has only {T['boxes_per_class']['raw_meat']} train boxes. `bin_open` ({O['boxes_per_class']['bin_open']}) and "
      f"`smoking` ({O['boxes_per_class']['smoking']}) are also small.",
      f"6. **Val scores for raw_meat, smoking and phone are unreliable.** Val has only {VA['boxes_per_class']['raw_meat']}, "
      f"{VA['boxes_per_class']['smoking']} and {VA['boxes_per_class']['phone']} boxes of these classes. "
      "One or two detections more or less changes their val AP a lot. Do not draw conclusions from their per-class val scores.",
      "7. **Merged class meanings.** `phone` now includes 13 boxes first labelled `phone_in_hand` (a phone in use). The remaining phone boxes "
      "were labelled as non-violations (`hard_negative`). The detector only finds phones; whether a phone is being used (H-04) is decided by the rule engine. "
      "Similarly, `mask_off` includes 2 `mask_incorrect` boxes; one of them appears to be drawn on a hand and is probably a labelling mistake (frame `R01_A_S12_20261106233714023_f007213`).",
      "8. **Label quality.** Labels were not double-checked by a second person (all `review_status` = pending). "
      "Some person boxes have no head box (2.9%) and 163 part boxes lie outside any person box.",
      "9. **Session numbers are unreliable** in the source annotations, so grouping uses clip and recording date instead (section 2).", ""]
R += ["## 8. Verification", "",
      f"**{int((V.result == 'PASS').sum())} of {len(V)} checks PASS, {int((V.result == 'FAIL').sum())} FAIL.** "
      "Route A = counted from the cleaned data table in memory. Route B = counted by re-reading the files written to `dataset/`: "
      "image files, label files, image sizes from the image files, `data.yaml`, `index.csv`, and for the renumbering check the original `annotations.json` files.", "",
      "**Renumbering check** (for each class: boxes with that *name* in the source annotations, minus removed boxes, plus renamed boxes, "
      "must equal the number of label lines with its *new id*):", "", md(REN), "",
      "**Ultralytics checks:**", "",
      f"- ultralytics version: {SMK.get('ultralytics_version', 'not run')}",
      *[f"- Ultralytics' own label scan ({m[0]}): {m[1]} images, {m[2]} backgrounds, {m[3]} corrupt; "
        f"expected {E[m[0]]['frames']} images, {E[m[0]]['background_frames']} backgrounds, 0 corrupt: "
        + ("PASS" if (int(m[1]), int(m[2]), int(m[3])) == (E[m[0]]['frames'], E[m[0]]['background_frames'], 0) else "FAIL")
        for m in SCAN],
      f"- `check_det_dataset(data.yaml)`: {SMK.get('check_det_dataset', 'not run')} (classes found: {SMK.get('check_det_dataset_nc', '')})",
      f"- 1-epoch smoke test `YOLO('yolo26s.pt').train(data=..., epochs=1, imgsz=640, batch=4, workers=0)`: "
      f"{SMK.get('smoke_test', 'not run')} on {SMK.get('device', '?')} in {SMK.get('smoke_minutes', '?')} min"
      + (f"; after 1 epoch: mAP50 {SMK['metrics'].get('metrics/mAP50(B)')}, mAP50-95 {SMK['metrics'].get('metrics/mAP50-95(B)')} "
         "(a smoke test only: it shows training runs, not model quality)" if SMK.get("metrics") else ""), "",
      "**All checks (both values):**", "", md(V), ""]
R += ["## How to train", "",
      "On the training laptop, first change the `path:` line in `dataset/data.yaml` to where the `dataset` folder is on that machine. Then:", "",
      f"```\n{train_cmd}\n```", "",
      "`amp=False` keeps training in FP32. If the GTX 1650 (4 GB) runs out of memory, lower `batch`.", ""]
(OUT / "DATA_RECORD.md").write_text("\n".join(R) + "\n")
print("DATA_RECORD.md written;", len(V), "checks")
