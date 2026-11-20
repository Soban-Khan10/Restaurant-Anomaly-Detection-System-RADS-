# RADS dataset audit and preparation (instructions for Claude Code)

You are preparing the RADS restaurant CCTV dataset for training an Ultralytics **YOLO26s** detector
(imgsz 640, FP32, trained on a GTX 1650 laptop). The current folder is `All_Yolo_Data` on the user's Desktop.
It holds every export from the team's annotation tool (RADS Annotator), as folders and/or ZIP files,
possibly nested several levels deep, possibly ZIPs inside ZIPs.

**All of this data is for training.** The test set is recorded separately later and is NOT in this folder.
Split this data into **train and val only** (no test split). Ignore any `split` value written by the annotator:
frames tagged `test` or `unassigned` are simply part of the pool to be split into train/val.

Work in **two phases**. Do **Phase 1** (read-only audit), write the report, then **STOP and wait** for the user
to confirm the split (and any other-class boxes, if found). Only then do **Phase 2** (build the training dataset).

## Hard rules
- **Never modify, move, rename or delete anything in `All_Yolo_Data`.** Treat it as read-only.
- Write everything you produce to a new folder next to it: `Desktop/RADS_YOLO_prepared/`
  (if it exists, create `RADS_YOLO_prepared_2`, etc.). Extract ZIPs into
  `RADS_YOLO_prepared/_work/extracted/<zip name>/`, never into the source folder.
- Never scan your own output folder as input.
- Map classes **by name, never by number**. Older exports used different class numbering.
- Use Python 3 with: pillow, numpy, pandas, matplotlib, pyyaml, imagehash (`pip install` what is missing).
  Put all scripts in `RADS_YOLO_prepared/scripts/` so the work can be re-run.
- Do not ask the user to re-annotate anything. Do not invent labels. Report problems instead of guessing.

## The export format you will find
Each export ZIP (or unzipped folder) from RADS Annotator contains:
- `frames/<frame_id>/<frame_id>_raw.jpg` (or `.png`): the original frame. **This is the training image.**
- `frames/<frame_id>/<frame_id>_annotated.jpg`: the same frame with boxes drawn, for humans only. Never train on it.
- `frames/<frame_id>/<frame_id>_metadata.json`: `frame_id`, `annotator`, `image{raw, annotated, width, height}`,
  optionally `split`, `group{restaurant_id, session_id, camera_view, camera_id, clip_file}`,
  `source{frame_index, timestamp_sec, fps}`, `is_background`, `num_boxes`,
  `boxes[{class_id, class_name, bbox_yolo:[cx,cy,w,h] normalised 0-1, ...}]`.
- `annotations.json` (top level of each export): the full record. `classes` list, `clips` (with recording details
  and `split`), `frames` (each with `frame_id`, `image_raw`, `source`, `image`, `recording`, `context`
  incl. `split` and `hazards_marked`, `annotation` incl. `annotator`, `updated_at`, `review_status`, and `boxes`
  with `class_name`, `bbox_xywh_px`, `bbox_yolo`, `hazard`, `status`, `worker_id`, `occluded`, `truncated`).
  **Prefer `annotations.json` as the source of truth** when it exists; fall back to the per-frame metadata files.
- Older exports may use a different layout: `images/raw/`, `images/annotated/`, `labels/`, `metadata/`.
  Support it too (match by frame_id = file stem).
- Large exports are split into `..._part1of5_...zip`, `part2of5`, … Each part is self-contained.
- `frame_id` format: `<restaurant>_<camera view>_<session>_<clip name>_f<6-digit frame number>`.
- Boxes in a frame with zero boxes = background image (valid; keep it, with an empty label file).

## Target classes (final YOLO ids, in this exact order)
```
0 person
1 head_covered
2 head_uncovered
3 mask_on
4 mask_off
5 apron
6 no_apron
7 hand_bare
8 hand_gloved
9 rte_food
10 raw_meat
11 smoking
12 phone
13 bin_closed
14 bin_open
```

The team used **only these 15 classes**. Nothing is merged or renamed. The only change is **renumbering**: in the
annotator the same classes have different numbers (with gaps, e.g. smoking = 14, phone = 16, bin_closed = 17,
bin_open = 18), so every label is rewritten with the new ids 0–14 above, matched **by class name**.

Safety check only: if any box uses a class name that is not one of the 15, do not change or drop it silently.
List it in the report (class name, count, frame_ids) and ask the user what to do. If none are found, write
"No other classes found" in the report.

---

## PHASE 1: audit (read-only), then STOP

### 1. Inventory
- Walk `All_Yolo_Data` recursively, at every depth. List every file with size. Find all ZIPs, including ZIPs inside
  ZIPs and inside extracted folders, and extract each into its own folder under `_work/extracted/`.
- For each export found, record: its path, layout (new `frames/` or old `images/`), `exported_by`, `exported_at`,
  number of frames, part number if any.
- Flag files you cannot read (corrupt ZIP, unreadable JSON, missing images).

### 2. Load every frame into one table
One row per (export, frame): frame_id, export, annotator, updated_at, image path, image width/height from metadata,
restaurant_id, session_id, camera_view, clip_file, frame_index, timestamp_sec, split, hazards, review_status,
and its boxes (class_name, bbox_yolo, bbox_px, status, hazard).

### 3. De-duplicate
- The same frame_id can appear in several exports (re-exports, teammates opening each other's ZIPs).
  Keep **one** copy per frame_id: the one with the latest `annotation.updated_at`
  (if unavailable, the latest `exported_at`).
- Report: how many duplicates, and every frame_id where the duplicates have **different boxes**
  (different class lists or any box moved by more than 2% of the image). List which annotators disagree.

### 4. Integrity checks (report counts and list the frame_ids)
- Raw image missing, unreadable, or its real pixel size differs from the metadata width/height.
- bbox_yolo values outside 0–1, or width/height ≤ 0. Box partly outside the image (report; clipping is allowed later).
- Tiny boxes: smaller than 4 × 4 pixels in the original image (probably accidental clicks).
- Exact duplicate boxes: same class and IoU > 0.9 in the same frame.
- **Conflicting labels**: two boxes of mutually exclusive classes overlapping with IoU > 0.5:
  head_covered vs head_uncovered, mask_on vs mask_off, apron vs no_apron, hand_bare vs hand_gloved,
  bin_closed vs bin_open.
- Boxes whose class is not one of the 15 classes (count per class name and frame_ids; expected: none).
- Images that are mostly black or frozen (mean brightness < 10, or near-zero variance).

### 5. Consistency checks (report rates, do not fix)
- Persons without a head label: `person` boxes containing no head_covered/head_uncovered box (≥ 60% of the head box
  inside the person box). Report the share. Some are legitimate (head out of view), many are suspicious.
- Head/mask/hand/apron boxes not inside any person box (could be fine, could be missed persons).
- Frames whose hazards_marked/violation boxes disagree with their boxes (e.g. tagged H-04 but no phone box).

### 6. Near-duplicate frames
- Compute a perceptual hash (imagehash.phash) of every raw image.
- Report clusters of near-identical frames (Hamming distance ≤ 4) per clip; the split in step 7 must keep each
  cluster on one side.
- Do not delete anything; just report how many near-duplicates exist per clip.

### 7. Train / val split proposal
- Report the `split` values the annotator wrote (for information only; they are not used).
- Build a table: rows = recording group (restaurant_id, session_id), columns = the 15 classes, values = box counts,
  plus frame count and camera views. Both camera angles of one session are one group.
- **Propose** a train/val split by whole groups: about **85% train / 15% val** of frames, chosen so that every one of
  the 15 classes appears in val with as many boxes as possible (aim for at least 20 boxes per class in val) while
  train keeps the large majority. A group is never split across train and val.
- If there are too few groups to do that (for example only 2–3 sessions, or a class that exists in only one session),
  propose a fallback: split those sessions **by time within each clip**: the last ~15% of each clip's timeline goes to
  val, with a 5-second gap between the last train frame and the first val frame so near-identical neighbouring frames
  never sit on both sides. Explain the fallback and its weakness in the report.
- Show the proposed table (frames and boxes per class for train and val) and list any class with fewer than 20 boxes
  in val. Do not apply it yet.
- Also save the perceptual hashes of all frames to `audit/train_hashes.csv` (frame_id, phash). They will be used later
  to check that the separately recorded test set contains no near-copies of training frames.

### 8. Statistics (on the cleaned, de-duplicated ORIGINAL frames, before any augmentation)
Write CSVs and a markdown summary with:
- Total frames, total boxes, background frames (count and %), frames per annotator, per restaurant, per session,
  per camera view, per clip.
- Boxes per class, overall and for the proposed train/val. Frames containing each class.
- Frames per hazard tag (H-01 … H-08) from hazards_marked / violation boxes, overall and for train/val.
- **Paired (yes/no) classes**, overall and for train/val: boxes and percentage for each side of
  head_covered / head_uncovered, mask_on / mask_off, apron / no_apron, hand_gloved / hand_bare,
  bin_closed / bin_open. Example row: `apron 412 (63%) | no_apron 241 (37%) | ratio 1.7 : 1`.
- Class imbalance: ratio of the largest to the smallest class; flag classes with fewer than 100 boxes in train.
- **Box size at training scale**: for each class, the box width and height after resizing the image so its longest
  side is 640 px (scale = 640 / max(width, height); for 2688×1520 that is about 0.238). Report median, 10th percentile,
  and the share of boxes smaller than 16 px and smaller than 8 px on the shorter side. Flag classes where more than
  25% of boxes are under 16 px: these will be hard to detect at imgsz 640.
- Image resolutions found (count per resolution).
- Charts (PNG): boxes per class, boxes per class for train vs val, box size distribution per class.

### 9. Visual spot check
- For 30 random frames (spread across annotators and classes) draw the boxes **from bbox_yolo** onto the raw image with
  the class name, save to `RADS_YOLO_prepared/audit/visual_check/`. This proves the YOLO coordinates line up.

### 10. Report and STOP
Write `RADS_YOLO_prepared/audit/AUDIT_REPORT.md` with: what was found, all issues ranked by importance with
counts and example frame_ids, the statistics tables, the result of the other-classes safety check,
and the proposed split. Then show the user a short summary and ask them to confirm:
1. what to do with boxes of any other class, only if the safety check found some,
2. the proposed train/val split (or the fallback),
3. what to do with conflicting labels and tiny boxes (default: drop tiny boxes; for conflicts, keep the frame but list
   it for the team to fix in the annotator, or exclude those frames).
**Do not start Phase 2 until the user answers.**

---

## PHASE 2: build the training dataset (only after the user confirms)

1. Renumber the 15 classes to ids 0–14 by name. Handle any other-class boxes as the user decided. Drop tiny boxes (< 4 × 4 px). Clip boxes to the
   image. Remove exact duplicate boxes.
2. Apply the confirmed train/val split.
3. Write the dataset to `RADS_YOLO_prepared/dataset/`:
   - `images/train|val/<frame_id>.<ext>`: copy of the raw image (keep the original file, no re-encoding).
   - `labels/train|val/<frame_id>.txt`: one line per box, `class_id cx cy w h`, six decimals, using the
     **new** ids 0–14. Background frames get an empty .txt file.
   - `data.yaml`:
     ```yaml
     path: <absolute path to RADS_YOLO_prepared/dataset>
     train: images/train
     val: images/val
     # test: added later, when the separately recorded test set is ready
     names:
       0: person
       ...
       14: bin_open
     ```
   - `index.csv`: frame_id, split, restaurant_id, session_id, camera_view, clip_file, frame_index, annotator,
     num_boxes, path of the training image, path of the label, path of the original raw image and annotated image in
     the source exports.
4. Verify:
   - every image has exactly one label file and vice versa; every class id is between 0 and 14;
   - **renumbering check**: for each of the 15 classes, the number of boxes with that class *name* in the source
     data equals the number of label lines with its *new id* in the written .txt files, and `data.yaml` names match
     the id list above exactly (same order, same spelling);
   - no recording group (and, in the fallback, no near-duplicate cluster) is in both train and val;
   - re-draw 15 random frames from the **written** label files into `dataset_check/` and confirm they line up;
   - if `ultralytics` is installed, run
     `from ultralytics.data.utils import check_det_dataset; check_det_dataset("<path>/data.yaml")`
     and a 1-epoch smoke test: `YOLO("yolo26s.pt").train(data=..., epochs=1, imgsz=640, batch=4, workers=0)`.
     Report any error. Skip the smoke test if there is no internet to download weights.
5. **Re-verify every number.** Compute each statistic twice, by two independent routes, and require them to match:
   (a) from the cleaned in-memory table, and (b) by re-reading the files actually written to `dataset/`
   (count image files, count label files, count lines per class id in the .txt files, read image sizes from disk).
   Also check: train frames + val frames = total frames; sum of boxes per class = total boxes; every frame_id appears
   exactly once. If anything differs, stop, find the cause, fix it, and recount. Write the result of every check
   (PASS/FAIL with both values) in the record.
6. Write the record files (plain, simple language; these go into the FYP report):
   - `RADS_YOLO_prepared/DATA_RECORD.md`, with these sections:
     1. **Summary**: date, what this dataset is, model it is for (YOLO26s, 640), number of frames and boxes, train/val.
     2. **Source data**: which exports were used (file names, who exported, export dates), annotators and how many
        frames each labelled, restaurants, sessions, camera views, clips, image resolution.
     3. **Cleaning**: every change made, with counts and the reason (duplicates removed, tiny boxes dropped,
        classes renamed or dropped, frames excluded). Also what was NOT changed.
     4. **Classes**: the 15 classes with their final ids 0–14 (and their ids in the annotation tool, for reference). State clearly that these ids are fixed:
        the trained model and the RADS rule engine use them, so they must never be reordered.
     5. **How the data was split and why**: the method actually used (by recording session, or the time-based fallback),
        the 85/15 target and the real percentages, why a random split was not used (neighbouring frames are near
        copies, so a random split would leak and inflate validation scores), and that the test set is recorded
        separately from different sessions and is not part of this dataset.
     6. **Statistics (original data, no augmentation)**, each as a table with overall / train / val columns:
        frames, boxes, background frames; boxes per class; frames containing each class; the paired yes/no classes
        with percentages and ratios; frames per hazard tag (H-01 to H-08); frames per camera view, session and annotator;
        box size at 640 px per class with the share under 16 px.
     7. **Known limitations**: small classes, small objects, class imbalance, any fallback split, anything the team
        should mention honestly in the report.
     8. **Verification**: the PASS/FAIL list from step 5.
   - `RADS_YOLO_prepared/stats/`: one CSV per table above (same numbers), plus the charts, so tables can be pasted
     into Word or Excel.
   Use exact counts (no rounding of counts; percentages to one decimal place).
7. Do **not** augment. Augmentation is a separate later step, applied to the train split only, and its numbers
   must be reported separately from the original-data statistics.

At the end, show the user: final frames and boxes for train and val, boxes per class per split, anything dropped and why,
and the exact command to start training.
