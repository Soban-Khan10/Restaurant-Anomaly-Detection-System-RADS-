# RADS training dataset: data record

This record describes how the RADS restaurant-CCTV training data was checked, cleaned and split. Every number below was computed twice: once from the cleaned data table, and once by re-reading the files written to `dataset/`. All pairs matched (section 8). Tables are also in `stats/` as CSV files.

## 1. Summary

- **Date prepared:** 2026-11-20
- **What it is:** labelled frames from restaurant CCTV video, for detecting food-safety hazards (head cover, mask, apron, gloves, food, smoking, phone, bins). Original frames only, no augmentation.
- **Model:** Ultralytics YOLO26s, image size 640, FP32, trained on a GTX 1650 laptop.
- **Size:** 1864 frames and 16677 boxes, in 15 classes.
- **Split:** train 1584 frames (85.0%) and 14296 boxes; val 280 frames (15.0%) and 2381 boxes. There is no test split: the test set will be recorded separately later.
- **Location:** `RADS_YOLO_prepared/dataset/` (`images/`, `labels/`, `data.yaml`, `index.csv`).

## 2. Source data

All exports from the RADS Annotator tool found in `All_Yolo_Data` were used: 23 exports (22 ZIP files plus 1 unzipped folder), all in the same export format (schema version 2). The source folder was only read, never changed.

| file | exported by | exported at (UTC) | frames in export | frames used |
|---|---|---|---|---|
| RADS_soban_2026-11-07-11-30_20frames/ (unzipped folder) | Soban Khan | 2026-11-07T11:30:39.597Z | 20 | 18 |
| Maham_FRAMES/RADS_basima_2026-11-14-22-50_part1of9_100frames.zip | Basima Maham | 2026-11-14T22:54:54.795Z | 100 | 100 |
| Maham_FRAMES/RADS_basima_2026-11-14-22-50_part2of9_100frames.zip | Basima Maham | 2026-11-14T22:57:11.688Z | 100 | 100 |
| Maham_FRAMES/RADS_basima_2026-11-14-22-50_part3of9_100frames.zip | Basima Maham | 2026-11-14T22:59:24.896Z | 100 | 100 |
| Maham_FRAMES/RADS_basima_2026-11-14-22-50_part4of9_100frames.zip | Basima Maham | 2026-11-14T23:01:38.518Z | 100 | 100 |
| Maham_FRAMES/RADS_basima_2026-11-14-22-50_part5of9_100frames.zip | Basima Maham | 2026-11-14T23:04:02.433Z | 100 | 99 |
| Maham_FRAMES/RADS_basima_2026-11-14-22-50_part6of9_100frames.zip | Basima Maham | 2026-11-14T23:06:26.422Z | 100 | 100 |
| Maham_FRAMES/RADS_basima_2026-11-14-22-50_part7of9_100frames.zip | Basima Maham | 2026-11-14T23:08:50.020Z | 100 | 100 |
| Maham_FRAMES/RADS_basima_2026-11-14-22-50_part8of9_100frames.zip | Basima Maham | 2026-11-14T23:11:14.291Z | 100 | 99 |
| Maham_FRAMES/RADS_basima_2026-11-14-22-50_part9of9_61frames.zip | Basima Maham | 2026-11-14T23:12:38.335Z | 61 | 61 |
| RADS_soban_2026-11-17-18-55_part1of7_100frames.zip | Soban Khan | 2026-11-17T18:55:53.880Z | 100 | 100 |
| RADS_soban_2026-11-17-18-55_part2of7_100frames.zip | Soban Khan | 2026-11-17T18:55:59.275Z | 100 | 100 |
| RADS_soban_2026-11-17-18-55_part3of7_100frames.zip | Soban Khan | 2026-11-17T18:56:04.686Z | 100 | 99 |
| RADS_soban_2026-11-17-18-55_part4of7_100frames.zip | Soban Khan | 2026-11-17T18:56:10.045Z | 100 | 99 |
| RADS_soban_2026-11-17-18-55_part5of7_100frames.zip | Soban Khan | 2026-11-17T18:56:15.418Z | 100 | 99 |
| RADS_soban_2026-11-17-18-55_part6of7_100frames.zip | Soban Khan | 2026-11-17T18:56:20.608Z | 100 | 100 |
| RADS_soban_2026-11-17-18-55_part7of7_14frames.zip | Soban Khan | 2026-11-17T18:56:22.195Z | 14 | 14 |
| Tahreem_Frames/RADS_tahreem_2026-11-18-12-40_62frames.zip | Tahreem Fatima | 2026-11-18T12:40:50.523Z | 62 | 62 |
| Tahreem_Frames/RADS_tahreem_2026-11-19-08-00_31frames.zip | Tahreem Fatima | 2026-11-19T08:00:05.564Z | 31 | 31 |
| Tahreem_Frames/RADS_tahreem_2026-11-19-09-04_37frames.zip | Tahreem Fatima | 2026-11-19T09:04:43.433Z | 37 | 37 |
| Tahreem_Frames/RADS_tahreem_2026-11-20-08-02_89frames.zip | Tahreem Fatima | 2026-11-20T08:02:18.470Z | 89 | 89 |
| Tahreem_Frames/RADS_tahreem_2026-11-20-08-51_59frames.zip | Tahreem Fatima | 2026-11-20T08:51:45.955Z | 59 | 59 |
| Tahreem_Frames/RADS_tahreem_2026-11-20-09-34_98frames.zip | Tahreem Fatima | 2026-11-20T09:34:34.391Z | 98 | 98 |

**Annotators and frames in the final dataset:** Basima Maham 859, Soban Khan 629, Tahreem Fatima 376.

**Restaurant:** one restaurant, `R01`. 98 frames carried the restaurant ID `R01cutte-t`. That is a typing error for R01 (same camera, same kitchen), so it was corrected to R01.

**Cameras:** camera view A = **camera 1** (high diagonal view), camera view B = **camera 2**. camera 1: 1740 frames, camera 2: 124 frames.

**Recordings:** 20 video clips, recorded on 2 evenings: 2026-11-06 (1269 frames), 2026-11-09 (595 frames). The recording date and start time come from each clip's file name (for example `20261109220937414` = 2026-11-09 22:09:37). The `recording_date` field in the annotation files holds the date the clip was added to the tool, not the recording date, so it was not used.

**Grouping:** frames are grouped by **video clip and recording date**. The annotators' `session_id` was not used: each annotator numbered sessions their own way, and many frames kept a default `S01`, so it does not identify a recording.

| clip | recording_date | recording start | camera | split | frames | boxes | annotators |
|---|---|---|---|---|---|---|---|
| 20261106221825500 | 2026-11-06 | 22:18:25 | camera 1 | train | 353 | 3830 | Soban Khan |
| 20261106224119984 | 2026-11-06 | 22:41:19 | camera 1 | train | 44 | 233 | Basima Maham, Soban Khan |
| 20261106230458823 | 2026-11-06 | 23:04:58 | camera 1 | val | 59 | 354 | Tahreem Fatima |
| 20261106231439615 | 2026-11-06 | 23:14:39 | camera 1 | train | 98 | 585 | Tahreem Fatima |
| 20261106232021417 | 2026-11-06 | 23:20:21 | camera 1 | val | 62 | 453 | Tahreem Fatima |
| 20261106233714023 | 2026-11-06 | 23:37:14 | camera 1 | train | 653 | 5506 | Basima Maham |
| 20261109215618452 | 2026-11-09 | 21:56:18 | camera 1 | train | 89 | 760 | Tahreem Fatima |
| 20261109215956846 | 2026-11-09 | 21:59:56 | camera 1 | val | 37 | 198 | Tahreem Fatima |
| 20261109220241282 | 2026-11-09 | 22:02:41 | camera 1 | val | 31 | 328 | Tahreem Fatima |
| 20261109220450926 | 2026-11-09 | 22:04:50 | camera 1 | val | 20 | 291 | Basima Maham |
| 20261109220937414 | 2026-11-09 | 22:09:37 | camera 2 | val | 34 | 395 | Basima Maham |
| 20261109221217337 | 2026-11-09 | 22:12:17 | camera 1 | train | 47 | 765 | Basima Maham |
| 20261109221438712 | 2026-11-09 | 22:14:38 | camera 2 | train | 44 | 610 | Basima Maham |
| 20261109221619105 | 2026-11-09 | 22:16:19 | camera 2 | train | 35 | 414 | Basima Maham |
| 20261109222119140 | 2026-11-09 | 22:21:19 | camera 1 | train | 45 | 245 | Soban Khan |
| 20261109222259436 | 2026-11-09 | 22:22:59 | camera 1 | train | 43 | 354 | Soban Khan |
| 20261109222354933 | 2026-11-09 | 22:23:54 | camera 1 | val | 26 | 298 | Soban Khan |
| 20261109222506906 | 2026-11-09 | 22:25:06 | camera 1 | train | 45 | 449 | Soban Khan |
| 20261109223019963 | 2026-11-09 | 22:30:19 | camera 1 | train | 88 | 545 | Soban Khan |
| 20261109223111084 | 2026-11-09 | 22:31:11 | camera 2 | val | 11 | 64 | Soban Khan |

**Image resolution:** 2688x1520 (1864 frames).

## 3. Cleaning

Changes made, with counts and reasons:

| change | count | reason |
|---|---|---|
| Duplicate frame_ids removed (same frame in two exports, or listed twice) | 2 frame copies | Keep one copy per frame: the copy with the latest annotation edit time (`updated_at`). `R01_A_S01_20261106224119984-E32396832-DS-2CD2043G1_f000000`: in two exports with different boxes (7 vs 6); kept Basima Maham's later edit, removed Soban Khan's older copy; `R01_A_S17_20261109221217337_f002075`: listed twice in `RADS_basima_2026-11-14-22-50_part8of9_100frames` with identical boxes, one copy kept |
| Same image saved under two frame_ids: older copy removed | 5 frames (33 boxes on the removed copies) | Same picture labelled twice (re-labelled under another session number, or a frozen video frame). Kept the latest-edited copy: removed `R01_A_S01_20261106224119984-E32396832-DS-2CD2043G1_f000724`, kept `R01_A_S11_20261106224119984-E32396832-DS-2CD2043G1_f000724`; removed `R01_A_S01_20261109222506906_f000923`, kept `R01_A_S04_20261109222506906_f000923`; removed `R01_A_S02_20261109222119140_f001518`, kept `R01_A_S02_20261109222119140_f001568`; removed `R01_A_S02_20261106221825500-E32396832-DS-2CD2043G1_f021995`, kept `R01_A_S02_20261106221825500-E32396832-DS-2CD2043G1_f022020`; removed `R01_A_S12_20261106233714023_f011476`, kept `R01_A_S12_20261106233714023_f011501` |
| Class renamed: phone_in_hand → phone | 14 boxes | Team decision: phone_in_hand is not one of the 15 classes; it is a phone. Renamed by name. |
| Class renamed: mask_incorrect → mask_off | 2 boxes | Team decision: an incorrectly worn mask counts as a mask violation. Renamed by name. |
| Tiny boxes dropped (< 4 px wide or high in the original image) | 2 boxes | Probably accidental clicks: rte_food 37.91×2.44 px in `R01_A_S02_20261106221825500-E32396832-DS-2CD2043G1_f008222`; rte_food 3.77×16.92 px in `R01_A_S02_20261106221825500-E32396832-DS-2CD2043G1_f015106` |
| Exact duplicate boxes removed (same class, IoU > 0.9) | 2 boxes | The same object drawn twice: phone in `R01_A_S01_20261109220241282_f003013`; bin_closed in `R01cutte-t_A_S22_20261106231439615-H264_f000786` |
| Boxes clipped to the image | 316 boxes | rounding only: largest overshoot 0.0013 px; no box was really outside the image |
| Restaurant ID R01cutte-t → R01 | 98 frames | Typing error. |
| Class numbers rewritten (renumbering) | all 16677 boxes | The annotation tool numbers classes with gaps (0–22). Every box was given its new id 0–14 by class name (section 4). |

**Not changed:**
- No frames were excluded apart from the duplicates above. All 9 background frames (no boxes) were kept with empty label files.
- No box was moved, resized or re-labelled, apart from the renames above. No labels were invented.
- Images are exact byte copies of the raw frames (checked by file hash). They were not resized or re-encoded, and no augmentation was applied.
- The annotators' own `split` tags (`train` / `unassigned`) were ignored.
- Label-quality findings from the audit were reported but **not** fixed: 73 of 2,478 person boxes (2.9%) have no head box, and 163 head/mask/hand/apron boxes lie outside any person box. See `audit/AUDIT_REPORT.md`.

## 4. Classes

The 15 classes and their final ids. **These ids are fixed.** The trained model and the RADS rule engine both use them, so they must never be reordered or renumbered. The annotation tool ids are shown for reference only.

| final id | class | id in RADS Annotator | also includes |
|---|---|---|---|
| 0 | person | 0 |  |
| 1 | head_covered | 1 |  |
| 2 | head_uncovered | 2 |  |
| 3 | mask_on | 3 |  |
| 4 | mask_off | 4 | mask_incorrect (tool id 5), renamed |
| 5 | apron | 6 |  |
| 6 | no_apron | 7 |  |
| 7 | hand_bare | 9 |  |
| 8 | hand_gloved | 10 |  |
| 9 | rte_food | 11 |  |
| 10 | raw_meat | 12 |  |
| 11 | smoking | 14 |  |
| 12 | phone | 16 | phone_in_hand (tool id 15), renamed |
| 13 | bin_closed | 17 |  |
| 14 | bin_open | 18 |  |

## 5. How the data was split and why

**Method: by whole video clip.** Each of the 20 video clips went entirely to train or entirely to val. A clip is never split.

**Why not a random split of frames:** frames next to each other in a video are almost identical. With a random split, near-copies of the same moment would sit in both train and val. Val scores would then look much better than the model really is (leakage).

**How the val clips were chosen:** every possible set of clips was tried (1,048,574 sets). These rules had to hold:
- val is 12–18% of the frames;
- train keeps at least 75% of the boxes of every class;
- at least one camera-2 clip is in val.

502 sets met all the rules. Among them, the chosen set (approved by the team) gives the most classes at least 10 val boxes (all 15 reach it), then the most val boxes for the weakest classes.

**Target and actual:** target 85% train / 15% val. Actual: 85.0% / 15.0% of frames, 85.7% / 14.3% of boxes.

**Val clips (8):**

| clip | recording_date | recording start | camera | frames | boxes |
|---|---|---|---|---|---|
| 20261106230458823 | 2026-11-06 | 23:04:58 | camera 1 | 59 | 354 |
| 20261106232021417 | 2026-11-06 | 23:20:21 | camera 1 | 62 | 453 |
| 20261109215956846 | 2026-11-09 | 21:59:56 | camera 1 | 37 | 198 |
| 20261109220241282 | 2026-11-09 | 22:02:41 | camera 1 | 31 | 328 |
| 20261109220450926 | 2026-11-09 | 22:04:50 | camera 1 | 20 | 291 |
| 20261109220937414 | 2026-11-09 | 22:09:37 | camera 2 | 34 | 395 |
| 20261109222354933 | 2026-11-09 | 22:23:54 | camera 1 | 26 | 298 |
| 20261109223111084 | 2026-11-09 | 22:31:11 | camera 2 | 11 | 64 |

**Test set:** not part of this dataset. It will be recorded separately, in different sessions, and used only for the final evaluation. `audit/train_hashes.csv` holds an image hash of every training frame, so the test set can be checked for near-copies of training frames.

## 6. Statistics (original data, no augmentation)

### Frames, boxes, background

| measure | overall | train | val |
|---|---|---|---|
| frames | 1864 | 1584 | 280 |
| boxes | 16677 | 14296 | 2381 |
| background frames (no boxes) | 9 | 6 | 3 |
| share of frames | 100.0% | 85.0% | 15.0% |

### Boxes per class

| id | class | overall | train | val | val share |
|---|---|---|---|---|---|
| 0 | person | 2472 | 2095 | 377 | 15.3% |
| 1 | head_covered | 847 | 706 | 141 | 16.6% |
| 2 | head_uncovered | 1585 | 1368 | 217 | 13.7% |
| 3 | mask_on | 818 | 729 | 89 | 10.9% |
| 4 | mask_off | 977 | 848 | 129 | 13.2% |
| 5 | apron | 765 | 587 | 178 | 23.3% |
| 6 | no_apron | 951 | 829 | 122 | 12.8% |
| 7 | hand_bare | 1727 | 1562 | 165 | 9.6% |
| 8 | hand_gloved | 1369 | 1108 | 261 | 19.1% |
| 9 | rte_food | 3395 | 2866 | 529 | 15.6% |
| 10 | raw_meat | 105 | 95 | 10 | 9.5% |
| 11 | smoking | 192 | 180 | 12 | 6.2% |
| 12 | phone | 327 | 304 | 23 | 7.0% |
| 13 | bin_closed | 980 | 885 | 95 | 9.7% |
| 14 | bin_open | 167 | 134 | 33 | 19.8% |

Class imbalance: largest class `rte_food` 3395 boxes vs smallest `raw_meat` 105 boxes, ratio 32.3 : 1. Classes with fewer than 100 train boxes: raw_meat (95).

### Frames containing each class

| id | class | overall | train | val |
|---|---|---|---|---|
| 0 | person | 1817 | 1550 | 267 |
| 1 | head_covered | 841 | 700 | 141 |
| 2 | head_uncovered | 1523 | 1306 | 217 |
| 3 | mask_on | 807 | 719 | 88 |
| 4 | mask_off | 968 | 839 | 129 |
| 5 | apron | 673 | 531 | 142 |
| 6 | no_apron | 942 | 820 | 122 |
| 7 | hand_bare | 1030 | 915 | 115 |
| 8 | hand_gloved | 853 | 673 | 180 |
| 9 | rte_food | 1315 | 1131 | 184 |
| 10 | raw_meat | 104 | 94 | 10 |
| 11 | smoking | 190 | 179 | 11 |
| 12 | phone | 326 | 303 | 23 |
| 13 | bin_closed | 979 | 884 | 95 |
| 14 | bin_open | 166 | 133 | 33 |

### Paired yes/no classes

| pair (yes / no) | overall | train | val |
|---|---|---|---|
| head_covered / head_uncovered | head_covered 847 (34.8%) · head_uncovered 1585 (65.2%) · ratio 1.9 : 1 | head_covered 706 (34.0%) · head_uncovered 1368 (66.0%) · ratio 1.9 : 1 | head_covered 141 (39.4%) · head_uncovered 217 (60.6%) · ratio 1.5 : 1 |
| mask_on / mask_off | mask_on 818 (45.6%) · mask_off 977 (54.4%) · ratio 1.2 : 1 | mask_on 729 (46.2%) · mask_off 848 (53.8%) · ratio 1.2 : 1 | mask_on 89 (40.8%) · mask_off 129 (59.2%) · ratio 1.4 : 1 |
| apron / no_apron | apron 765 (44.6%) · no_apron 951 (55.4%) · ratio 1.2 : 1 | apron 587 (41.5%) · no_apron 829 (58.5%) · ratio 1.4 : 1 | apron 178 (59.3%) · no_apron 122 (40.7%) · ratio 1.5 : 1 |
| hand_gloved / hand_bare | hand_gloved 1369 (44.2%) · hand_bare 1727 (55.8%) · ratio 1.3 : 1 | hand_gloved 1108 (41.5%) · hand_bare 1562 (58.5%) · ratio 1.4 : 1 | hand_gloved 261 (61.3%) · hand_bare 165 (38.7%) · ratio 1.6 : 1 |
| bin_closed / bin_open | bin_closed 980 (85.4%) · bin_open 167 (14.6%) · ratio 5.9 : 1 | bin_closed 885 (86.8%) · bin_open 134 (13.2%) · ratio 6.6 : 1 | bin_closed 95 (74.2%) · bin_open 33 (25.8%) · ratio 2.9 : 1 |

### Frames per hazard

H-01, H-03, H-05, H-06 and H-08 each have their own violation class, so the count is the frames where that class was labelled. These counts equal the annotators' hazard tags exactly (checked). **H-02, H-04 and H-07 are derived by the RADS rule engine, not taken from annotator tags.** They have no violation class of their own: the rule engine decides them from combinations of classes (and, for H-07, the order of events in time). For these three the table shows only how many frames contain the input classes the rule engine needs. That is not the number of violations.

| hazard | name | source | overall | train | val |
|---|---|---|---|---|---|
| H-01 | Missing hair restraint / head cover | head_uncovered box (annotator tag) | 1523 | 1306 | 217 |
| H-02 | Bare-hand contact with RTE food | DERIVED by rule engine; frames with hand_bare + rte_food | 685 | 611 | 74 |
| H-03 | Smoking / vaping in prep area | smoking box (annotator tag) | 190 | 179 | 11 |
| H-04 | Phone use during food handling | DERIVED by rule engine; frames with a phone box | 326 | 303 | 23 |
| H-05 | Open / overflowing waste bin | bin_open box (annotator tag) | 166 | 133 | 33 |
| H-06 | Missing / soiled apron or uniform | no_apron box (annotator tag) | 942 | 820 | 122 |
| H-07 | No hand hygiene: raw meat -> RTE | DERIVED by rule engine; frames with raw_meat + a hand box (needs the time sequence to decide) | 104 | 94 | 10 |
| H-08 | Missing required face mask | mask_off box (annotator tag) | 968 | 839 | 129 |

### Frames per camera

| camera | overall | train | val |
|---|---|---|---|
| camera 1 | 1740 | 1505 | 235 |
| camera 2 | 124 | 79 | 45 |

### Frames per recording date

| recording date | overall | train | val |
|---|---|---|---|
| 2026-11-06 | 1269 | 1148 | 121 |
| 2026-11-09 | 595 | 436 | 159 |

### Frames per annotator

| annotator | overall | train | val |
|---|---|---|---|
| Basima Maham | 859 | 805 | 54 |
| Soban Khan | 629 | 592 | 37 |
| Tahreem Fatima | 376 | 187 | 189 |

(Frames per clip: see section 2.)

### Box size at training scale (640 px)

Each image is resized so its longest side is 640 px (2688×1520 → 640×362, scale 0.238). Sizes are in pixels at that scale. A box whose shorter side is under 16 px is hard for the detector to find.

| id | class | boxes | median w | median h | p10 w | p10 h | % short side < 16 px (overall) | % < 16 px (train) | % < 16 px (val) | % short side < 8 px (overall) | hard at 640? |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | person | 2472 | 107.4 | 197.6 | 71.1 | 107.7 | 1.1 | 0.5 | 4.2 | 1.1 |  |
| 1 | head_covered | 847 | 41.1 | 32.8 | 26.9 | 18.3 | 4.7 | 5.5 | 0.7 | 0.0 |  |
| 2 | head_uncovered | 1585 | 44.5 | 38.8 | 28.7 | 25.1 | 0.3 | 0.2 | 0.5 | 0.0 |  |
| 3 | mask_on | 818 | 28.9 | 23.1 | 14.0 | 12.6 | 26.7 | 25.0 | 40.4 | 2.9 | YES |
| 4 | mask_off | 977 | 26.2 | 22.1 | 13.3 | 11.4 | 32.7 | 29.5 | 53.5 | 3.9 | YES |
| 5 | apron | 765 | 50.5 | 121.4 | 33.4 | 78.8 | 0.1 | 0.2 | 0.0 | 0.0 |  |
| 6 | no_apron | 951 | 50.1 | 101.7 | 27.3 | 71.0 | 1.1 | 1.2 | 0.0 | 0.0 |  |
| 7 | hand_bare | 1727 | 20.2 | 18.6 | 13.8 | 11.6 | 43.6 | 43.2 | 47.3 | 0.6 | YES |
| 8 | hand_gloved | 1369 | 24.8 | 21.4 | 15.9 | 12.7 | 27.2 | 30.1 | 14.9 | 1.2 | YES |
| 9 | rte_food | 3395 | 23.1 | 17.7 | 13.0 | 10.3 | 44.8 | 40.4 | 68.4 | 4.2 | YES |
| 10 | raw_meat | 105 | 10.2 | 16.7 | 7.9 | 9.4 | 93.3 | 92.6 | 100.0 | 15.2 | YES |
| 11 | smoking | 192 | 7.2 | 7.8 | 4.2 | 4.3 | 99.0 | 98.9 | 100.0 | 71.4 | YES |
| 12 | phone | 327 | 20.0 | 16.1 | 13.3 | 9.7 | 55.7 | 53.9 | 78.3 | 5.5 | YES |
| 13 | bin_closed | 980 | 25.2 | 67.0 | 23.4 | 62.3 | 0.4 | 0.5 | 0.0 | 0.0 |  |
| 14 | bin_open | 167 | 56.9 | 98.5 | 43.8 | 80.7 | 0.6 | 0.7 | 0.0 | 0.6 |  |

Charts: `stats/chart_boxes_per_class.png`, `stats/chart_boxes_per_class_train_val.png`, `stats/chart_box_size_per_class.png`.

## 7. Known limitations

These should be stated honestly in the project report.

1. **One restaurant, one kitchen.** All frames come from restaurant R01. The model has not seen other kitchens, layouts or lighting.
2. **Two evenings only.** All clips were recorded on 2026-11-06, 2026-11-09. Camera 2 has only 124 frames (6.7%).
3. **Same people and scene in train and val.** The same small group of staff/actors appears in all clips, in the same kitchen, with the same fixed cameras. Train and val use different clips, but the scenes still look very alike: 247 of 280 val frames have a train frame from another clip that is nearly identical by image hash (whole-frame phash, distance ≤ 4). Val scores will therefore be **optimistic**. Only the separately recorded test set (new sessions) measures real performance.
4. **Small objects.** At imgsz 640, 8 classes have more than 25% of boxes under 16 px on the shorter side: mask_on (26.7%), mask_off (32.7%), hand_bare (43.6%), hand_gloved (27.2%), rte_food (44.8%), raw_meat (93.3%), smoking (99.0%), phone (55.7%). `smoking` is the hardest: median box about 7.2×7.8 px, and 71.4% of boxes are under 8 px. Expect low recall for these classes at 640. A larger image size or tiled inference would help but needs more GPU memory than the GTX 1650 has.
5. **Class imbalance.** `rte_food` has 32.3× more boxes than `raw_meat`. `raw_meat` has only 95 train boxes. `bin_open` (167) and `smoking` (192) are also small.
6. **Val scores for raw_meat, smoking and phone are unreliable.** Val has only 10, 12 and 23 boxes of these classes. One or two detections more or less changes their val AP a lot. Do not draw conclusions from their per-class val scores.
7. **Merged class meanings.** `phone` now includes 13 boxes first labelled `phone_in_hand` (a phone in use). The remaining phone boxes were labelled as non-violations (`hard_negative`). The detector only finds phones; whether a phone is being used (H-04) is decided by the rule engine. Similarly, `mask_off` includes 2 `mask_incorrect` boxes; one of them appears to be drawn on a hand and is probably a labelling mistake (frame `R01_A_S12_20261106233714023_f007213`).
8. **Label quality.** Labels were not double-checked by a second person (all `review_status` = pending). Some person boxes have no head box (2.9%) and 163 part boxes lie outside any person box.
9. **Session numbers are unreliable** in the source annotations, so grouping uses clip and recording date instead (section 2).

## 8. Verification

**160 of 160 checks PASS, 0 FAIL.** Route A = counted from the cleaned data table in memory. Route B = counted by re-reading the files written to `dataset/`: image files, label files, image sizes from the image files, `data.yaml`, `index.csv`, and for the renumbering check the original `annotations.json` files.

**Renumbering check** (for each class: boxes with that *name* in the source annotations, minus removed boxes, plus renamed boxes, must equal the number of label lines with its *new id*):

| class | new_id | source_boxes_with_this_name | removed_tiny_or_duplicate | renamed_into_this_class | expected_label_lines | label_lines_with_new_id | result |
|---|---|---|---|---|---|---|---|
| person | 0 | 2472 | 0 | 0 | 2472 | 2472 | PASS |
| head_covered | 1 | 847 | 0 | 0 | 847 | 847 | PASS |
| head_uncovered | 2 | 1585 | 0 | 0 | 1585 | 1585 | PASS |
| mask_on | 3 | 818 | 0 | 0 | 818 | 818 | PASS |
| mask_off | 4 | 975 | 0 | 2 | 977 | 977 | PASS |
| apron | 5 | 765 | 0 | 0 | 765 | 765 | PASS |
| no_apron | 6 | 951 | 0 | 0 | 951 | 951 | PASS |
| hand_bare | 7 | 1727 | 0 | 0 | 1727 | 1727 | PASS |
| hand_gloved | 8 | 1369 | 0 | 0 | 1369 | 1369 | PASS |
| rte_food | 9 | 3397 | 2 | 0 | 3395 | 3395 | PASS |
| raw_meat | 10 | 105 | 0 | 0 | 105 | 105 | PASS |
| smoking | 11 | 192 | 0 | 0 | 192 | 192 | PASS |
| phone | 12 | 314 | 0 | 13 | 327 | 327 | PASS |
| bin_closed | 13 | 981 | 1 | 0 | 980 | 980 | PASS |
| bin_open | 14 | 167 | 0 | 0 | 167 | 167 | PASS |

**Ultralytics checks:**

- ultralytics version: 8.4.216
- Ultralytics' own label scan (train): 1584 images, 6 backgrounds, 0 corrupt; expected 1584 images, 6 backgrounds, 0 corrupt: PASS
- Ultralytics' own label scan (val): 280 images, 3 backgrounds, 0 corrupt; expected 280 images, 3 backgrounds, 0 corrupt: PASS
- `check_det_dataset(data.yaml)`: OK (classes found: 15)
- 1-epoch smoke test `YOLO('yolo26s.pt').train(data=..., epochs=1, imgsz=640, batch=4, workers=0)`: OK on cpu in 8.7 min; after 1 epoch: mAP50 0.4449, mAP50-95 0.2615 (a smoke test only: it shows training runs, not model quality)

**All checks (both values):**

| check | route_A_in_memory | route_B_from_disk | result |
|---|---|---|---|
| train: image files == label files (count) | 1584 | 1584 | PASS |
| train: every image has exactly one label and vice versa | 0 | 0 | PASS |
| val: image files == label files (count) | 280 | 280 | PASS |
| val: every image has exactly one label and vice versa | 0 | 0 | PASS |
| label lines well-formed (5 fields, id 0-14, 6 decimals, inside image, w/h > 0): bad lines | 0 | 0 | PASS |
| all class ids between 0 and 14 | True | True | PASS |
| data.yaml names == fixed id list (order and spelling) | {"0": "person", "1": "head_covered", "2": "head_uncovered", "3": "mask_on", "4": "mask_off", "5": "apron", "6": "no_apron", "7": "hand_bare", "8": "hand_gloved", "9": "rte_food", "10": "raw_meat", "11": "smoking", "12": "phone", "13": "bin_closed", "14": "bin_open"} | {"0": "person", "1": "head_covered", "2": "head_uncovered", "3": "mask_on", "4": "mask_off", "5": "apron", "6": "no_apron", "7": "hand_bare", "8": "hand_gloved", "9": "rte_food", "10": "raw_meat", "11": "smoking", "12": "phone", "13": "bin_closed", "14": "bin_open"} | PASS |
| data.yaml train/val paths | ["images/train", "images/val", "/Users/user/Desktop/RADS_YOLO_prepared/dataset"] | ["images/train", "images/val", "/Users/user/Desktop/RADS_YOLO_prepared/dataset"] | PASS |
| data.yaml has no test entry yet | False | False | PASS |
| index.csv split matches folder | 0 | 0 | PASS |
| index.csv rows == image files | 1864 | 1864 | PASS |
| every frame_id appears exactly once (across train+val) | 1864 | 1864 | PASS |
| overall: frames | 1864 | 1864 | PASS |
| overall: boxes | 16677 | 16677 | PASS |
| overall: background_frames | 9 | 9 | PASS |
| overall: boxes of person (id 0) | 2472 | 2472 | PASS |
| overall: boxes of head_covered (id 1) | 847 | 847 | PASS |
| overall: boxes of head_uncovered (id 2) | 1585 | 1585 | PASS |
| overall: boxes of mask_on (id 3) | 818 | 818 | PASS |
| overall: boxes of mask_off (id 4) | 977 | 977 | PASS |
| overall: boxes of apron (id 5) | 765 | 765 | PASS |
| overall: boxes of no_apron (id 6) | 951 | 951 | PASS |
| overall: boxes of hand_bare (id 7) | 1727 | 1727 | PASS |
| overall: boxes of hand_gloved (id 8) | 1369 | 1369 | PASS |
| overall: boxes of rte_food (id 9) | 3395 | 3395 | PASS |
| overall: boxes of raw_meat (id 10) | 105 | 105 | PASS |
| overall: boxes of smoking (id 11) | 192 | 192 | PASS |
| overall: boxes of phone (id 12) | 327 | 327 | PASS |
| overall: boxes of bin_closed (id 13) | 980 | 980 | PASS |
| overall: boxes of bin_open (id 14) | 167 | 167 | PASS |
| overall: frames containing person | 1817 | 1817 | PASS |
| overall: frames containing head_covered | 841 | 841 | PASS |
| overall: frames containing head_uncovered | 1523 | 1523 | PASS |
| overall: frames containing mask_on | 807 | 807 | PASS |
| overall: frames containing mask_off | 968 | 968 | PASS |
| overall: frames containing apron | 673 | 673 | PASS |
| overall: frames containing no_apron | 942 | 942 | PASS |
| overall: frames containing hand_bare | 1030 | 1030 | PASS |
| overall: frames containing hand_gloved | 853 | 853 | PASS |
| overall: frames containing rte_food | 1315 | 1315 | PASS |
| overall: frames containing raw_meat | 104 | 104 | PASS |
| overall: frames containing smoking | 190 | 190 | PASS |
| overall: frames containing phone | 326 | 326 | PASS |
| overall: frames containing bin_closed | 979 | 979 | PASS |
| overall: frames containing bin_open | 166 | 166 | PASS |
| overall: frames_per_camera | {"camera 1": 1740, "camera 2": 124} | {"camera 1": 1740, "camera 2": 124} | PASS |
| overall: frames_per_clip | {"20261106221825500": 353, "20261106224119984": 44, "20261106230458823": 59, "20261106231439615": 98, "20261106232021417": 62, "20261106233714023": 653, "20261109215618452": 89, "20261109215956846": 37, "20261109220241282": 31, "20261109220450926": 20, "20261109220937414": 34, "20261109221217337": 47, "20261109221438712": 44, "20261109221619105": 35, "20261109222119140": 45, "20261109222259436": 43, "20261109222354933": 26, "20261109222506906": 45, "20261109223019963": 88, "20261109223111084": 11} | {"20261106221825500": 353, "20261106224119984": 44, "20261106230458823": 59, "20261106231439615": 98, "20261106232021417": 62, "20261106233714023": 653, "20261109215618452": 89, "20261109215956846": 37, "20261109220241282": 31, "20261109220450926": 20, "20261109220937414": 34, "20261109221217337": 47, "20261109221438712": 44, "20261109221619105": 35, "20261109222119140": 45, "20261109222259436": 43, "20261109222354933": 26, "20261109222506906": 45, "20261109223019963": 88, "20261109223111084": 11} | PASS |
| overall: frames_per_recording_date | {"2026-11-06": 1269, "2026-11-09": 595} | {"2026-11-06": 1269, "2026-11-09": 595} | PASS |
| overall: frames_per_annotator | {"Basima Maham": 859, "Soban Khan": 629, "Tahreem Fatima": 376} | {"Basima Maham": 859, "Soban Khan": 629, "Tahreem Fatima": 376} | PASS |
| overall: resolutions | {"2688x1520": 1864} | {"2688x1520": 1864} | PASS |
| overall: hazard frames (A: annotator tags, B: violation classes in label files) | {"H-01": 1523, "H-03": 190, "H-05": 166, "H-06": 942, "H-08": 968} | {"H-01": 1523, "H-03": 190, "H-05": 166, "H-06": 942, "H-08": 968} | PASS |
| overall: rule-engine input frames H-02/H-04/H-07 | {"H-02": 685, "H-04": 326, "H-07": 104} | {"H-02": 685, "H-04": 326, "H-07": 104} | PASS |
| overall: box size at 640 per class | {"person": {"median_w": 107.4, "median_h": 197.6, "p10_w": 71.1, "p10_h": 107.7, "pct_short_lt16": 1.1, "pct_short_lt8": 1.1}, "head_covered": {"median_w": 41.1, "median_h": 32.8, "p10_w": 26.9, "p10_h": 18.3, "pct_short_lt16": 4.7, "pct_short_lt8": 0.0}, "head_uncovered": {"median_w": 44.5, "median_h": 38.8, "p10_w": 28.7, "p10_h": 25.1, "pct_short_lt16": 0.3, "pct_short_lt8": 0.0}, "mask_on": {"median_w": 28.9, "median_h": 23.1, "p10_w": 14.0, "p10_h": 12.6, "pct_short_lt16": 26.7, "pct_short_lt8": 2.9}, "mask_off": {"median_w": 26.2, "median_h": 22.1, "p10_w": 13.3, "p10_h": 11.4, "pct_short_lt16": 32.7, "pct_short_lt8": 3.9}, "apron": {"median_w": 50.5, "median_h": 121.4, "p10_w": 33.4, "p10_h": 78.8, "pct_short_lt16": 0.1, "pct_short_lt8": 0.0}, "no_apron": {"median_w": 50.1, "median_h": 101.7, "p10_w": 27.3, "p10_h": 71.0, "pct_short_lt16": 1.1, "pct_short_lt8": 0.0}, "hand_bare": {"median_w": 20.2, "median_h": 18.6, "p10_w": 13.8, "p10_h": 11.6, "pct_short_lt16": 43.6, "pct_short_lt8": 0.6}, "hand_gloved": {"median_w": 24.8, "median_h": 21.4, "p10_w": 15.9, "p10_h": 12.7, "pct_short_lt16": 27.2, "pct_short_lt8": 1.2}, "rte_food": {"median_w": 23.1, "median_h": 17.7, "p10_w": 13.0, "p10_h": 10.3, "pct_short_lt16": 44.8, "pct_short_lt8": 4.2}, "raw_meat": {"median_w": 10.2, "median_h": 16.7, "p10_w": 7.9, "p10_h": 9.4, "pct_short_lt16": 93.3, "pct_short_lt8": 15.2}, "smoking": {"median_w": 7.2, "median_h": 7.8, "p10_w": 4.2, "p10_h": 4.3, "pct_short_lt16": 99.0, "pct_short_lt8": 71.4}, "phone": {"median_w": 20.0, "median_h": 16.1, "p10_w": 13.3, "p10_h": 9.7, "pct_short_lt16": 55.7, "pct_short_lt8": 5.5}, "bin_closed": {"median_w": 25.2, "median_h": 67.0, "p10_w": 23.4, "p10_h": 62.3, "pct_short_lt16": 0.4, "pct_short_lt8": 0.0}, "bin_open": {"median_w": 56.9, "median_h": 98.5, "p10_w": 43.8, "p10_h": 80.7, "pct_short_lt16": 0.6, "pct_short_lt8": 0.6}} | {"person": {"median_w": 107.4, "median_h": 197.6, "p10_w": 71.1, "p10_h": 107.7, "pct_short_lt16": 1.1, "pct_short_lt8": 1.1}, "head_covered": {"median_w": 41.1, "median_h": 32.8, "p10_w": 26.9, "p10_h": 18.3, "pct_short_lt16": 4.7, "pct_short_lt8": 0.0}, "head_uncovered": {"median_w": 44.5, "median_h": 38.8, "p10_w": 28.7, "p10_h": 25.1, "pct_short_lt16": 0.3, "pct_short_lt8": 0.0}, "mask_on": {"median_w": 28.9, "median_h": 23.1, "p10_w": 14.0, "p10_h": 12.6, "pct_short_lt16": 26.7, "pct_short_lt8": 2.9}, "mask_off": {"median_w": 26.2, "median_h": 22.1, "p10_w": 13.3, "p10_h": 11.4, "pct_short_lt16": 32.7, "pct_short_lt8": 3.9}, "apron": {"median_w": 50.5, "median_h": 121.4, "p10_w": 33.4, "p10_h": 78.8, "pct_short_lt16": 0.1, "pct_short_lt8": 0.0}, "no_apron": {"median_w": 50.1, "median_h": 101.7, "p10_w": 27.3, "p10_h": 71.0, "pct_short_lt16": 1.1, "pct_short_lt8": 0.0}, "hand_bare": {"median_w": 20.2, "median_h": 18.6, "p10_w": 13.8, "p10_h": 11.6, "pct_short_lt16": 43.6, "pct_short_lt8": 0.6}, "hand_gloved": {"median_w": 24.8, "median_h": 21.4, "p10_w": 15.9, "p10_h": 12.7, "pct_short_lt16": 27.2, "pct_short_lt8": 1.2}, "rte_food": {"median_w": 23.1, "median_h": 17.7, "p10_w": 13.0, "p10_h": 10.3, "pct_short_lt16": 44.8, "pct_short_lt8": 4.2}, "raw_meat": {"median_w": 10.2, "median_h": 16.7, "p10_w": 7.9, "p10_h": 9.4, "pct_short_lt16": 93.3, "pct_short_lt8": 15.2}, "smoking": {"median_w": 7.2, "median_h": 7.8, "p10_w": 4.2, "p10_h": 4.3, "pct_short_lt16": 99.0, "pct_short_lt8": 71.4}, "phone": {"median_w": 20.0, "median_h": 16.1, "p10_w": 13.3, "p10_h": 9.7, "pct_short_lt16": 55.7, "pct_short_lt8": 5.5}, "bin_closed": {"median_w": 25.2, "median_h": 67.0, "p10_w": 23.4, "p10_h": 62.3, "pct_short_lt16": 0.4, "pct_short_lt8": 0.0}, "bin_open": {"median_w": 56.9, "median_h": 98.5, "p10_w": 43.8, "p10_h": 80.7, "pct_short_lt16": 0.6, "pct_short_lt8": 0.6}} | PASS |
| train: frames | 1584 | 1584 | PASS |
| train: boxes | 14296 | 14296 | PASS |
| train: background_frames | 6 | 6 | PASS |
| train: boxes of person (id 0) | 2095 | 2095 | PASS |
| train: boxes of head_covered (id 1) | 706 | 706 | PASS |
| train: boxes of head_uncovered (id 2) | 1368 | 1368 | PASS |
| train: boxes of mask_on (id 3) | 729 | 729 | PASS |
| train: boxes of mask_off (id 4) | 848 | 848 | PASS |
| train: boxes of apron (id 5) | 587 | 587 | PASS |
| train: boxes of no_apron (id 6) | 829 | 829 | PASS |
| train: boxes of hand_bare (id 7) | 1562 | 1562 | PASS |
| train: boxes of hand_gloved (id 8) | 1108 | 1108 | PASS |
| train: boxes of rte_food (id 9) | 2866 | 2866 | PASS |
| train: boxes of raw_meat (id 10) | 95 | 95 | PASS |
| train: boxes of smoking (id 11) | 180 | 180 | PASS |
| train: boxes of phone (id 12) | 304 | 304 | PASS |
| train: boxes of bin_closed (id 13) | 885 | 885 | PASS |
| train: boxes of bin_open (id 14) | 134 | 134 | PASS |
| train: frames containing person | 1550 | 1550 | PASS |
| train: frames containing head_covered | 700 | 700 | PASS |
| train: frames containing head_uncovered | 1306 | 1306 | PASS |
| train: frames containing mask_on | 719 | 719 | PASS |
| train: frames containing mask_off | 839 | 839 | PASS |
| train: frames containing apron | 531 | 531 | PASS |
| train: frames containing no_apron | 820 | 820 | PASS |
| train: frames containing hand_bare | 915 | 915 | PASS |
| train: frames containing hand_gloved | 673 | 673 | PASS |
| train: frames containing rte_food | 1131 | 1131 | PASS |
| train: frames containing raw_meat | 94 | 94 | PASS |
| train: frames containing smoking | 179 | 179 | PASS |
| train: frames containing phone | 303 | 303 | PASS |
| train: frames containing bin_closed | 884 | 884 | PASS |
| train: frames containing bin_open | 133 | 133 | PASS |
| train: frames_per_camera | {"camera 1": 1505, "camera 2": 79} | {"camera 1": 1505, "camera 2": 79} | PASS |
| train: frames_per_clip | {"20261106221825500": 353, "20261106224119984": 44, "20261106231439615": 98, "20261106233714023": 653, "20261109215618452": 89, "20261109221217337": 47, "20261109221438712": 44, "20261109221619105": 35, "20261109222119140": 45, "20261109222259436": 43, "20261109222506906": 45, "20261109223019963": 88} | {"20261106221825500": 353, "20261106224119984": 44, "20261106231439615": 98, "20261106233714023": 653, "20261109215618452": 89, "20261109221217337": 47, "20261109221438712": 44, "20261109221619105": 35, "20261109222119140": 45, "20261109222259436": 43, "20261109222506906": 45, "20261109223019963": 88} | PASS |
| train: frames_per_recording_date | {"2026-11-06": 1148, "2026-11-09": 436} | {"2026-11-06": 1148, "2026-11-09": 436} | PASS |
| train: frames_per_annotator | {"Basima Maham": 805, "Soban Khan": 592, "Tahreem Fatima": 187} | {"Basima Maham": 805, "Soban Khan": 592, "Tahreem Fatima": 187} | PASS |
| train: resolutions | {"2688x1520": 1584} | {"2688x1520": 1584} | PASS |
| train: hazard frames (A: annotator tags, B: violation classes in label files) | {"H-01": 1306, "H-03": 179, "H-05": 133, "H-06": 820, "H-08": 839} | {"H-01": 1306, "H-03": 179, "H-05": 133, "H-06": 820, "H-08": 839} | PASS |
| train: rule-engine input frames H-02/H-04/H-07 | {"H-02": 611, "H-04": 303, "H-07": 94} | {"H-02": 611, "H-04": 303, "H-07": 94} | PASS |
| train: box size at 640 per class | {"person": {"median_w": 107.4, "median_h": 199.3, "p10_w": 71.7, "p10_h": 107.5, "pct_short_lt16": 0.5, "pct_short_lt8": 0.5}, "head_covered": {"median_w": 40.5, "median_h": 31.2, "p10_w": 27.1, "p10_h": 17.6, "pct_short_lt16": 5.5, "pct_short_lt8": 0.0}, "head_uncovered": {"median_w": 44.2, "median_h": 38.4, "p10_w": 29.1, "p10_h": 25.4, "pct_short_lt16": 0.2, "pct_short_lt8": 0.0}, "mask_on": {"median_w": 28.7, "median_h": 23.1, "p10_w": 14.3, "p10_h": 13.4, "pct_short_lt16": 25.0, "pct_short_lt8": 2.1}, "mask_off": {"median_w": 26.7, "median_h": 22.6, "p10_w": 12.9, "p10_h": 11.4, "pct_short_lt16": 29.5, "pct_short_lt8": 4.4}, "apron": {"median_w": 49.9, "median_h": 121.4, "p10_w": 34.5, "p10_h": 83.9, "pct_short_lt16": 0.2, "pct_short_lt8": 0.0}, "no_apron": {"median_w": 49.0, "median_h": 100.0, "p10_w": 26.9, "p10_h": 70.8, "pct_short_lt16": 1.2, "pct_short_lt8": 0.0}, "hand_bare": {"median_w": 20.2, "median_h": 18.6, "p10_w": 14.0, "p10_h": 11.7, "pct_short_lt16": 43.2, "pct_short_lt8": 0.6}, "hand_gloved": {"median_w": 24.1, "median_h": 20.8, "p10_w": 15.1, "p10_h": 12.4, "pct_short_lt16": 30.1, "pct_short_lt8": 1.4}, "rte_food": {"median_w": 23.3, "median_h": 18.6, "p10_w": 12.7, "p10_h": 10.0, "pct_short_lt16": 40.4, "pct_short_lt8": 4.9}, "raw_meat": {"median_w": 10.0, "median_h": 16.5, "p10_w": 7.6, "p10_h": 9.4, "pct_short_lt16": 92.6, "pct_short_lt8": 16.8}, "smoking": {"median_w": 7.7, "median_h": 8.0, "p10_w": 4.3, "p10_h": 4.6, "pct_short_lt16": 98.9, "pct_short_lt8": 69.4}, "phone": {"median_w": 20.7, "median_h": 17.1, "p10_w": 13.3, "p10_h": 9.8, "pct_short_lt16": 53.9, "pct_short_lt8": 5.6}, "bin_closed": {"median_w": 25.2, "median_h": 66.9, "p10_w": 23.6, "p10_h": 63.2, "pct_short_lt16": 0.5, "pct_short_lt8": 0.0}, "bin_open": {"median_w": 60.4, "median_h": 98.5, "p10_w": 45.2, "p10_h": 80.7, "pct_short_lt16": 0.7, "pct_short_lt8": 0.7}} | {"person": {"median_w": 107.4, "median_h": 199.3, "p10_w": 71.7, "p10_h": 107.5, "pct_short_lt16": 0.5, "pct_short_lt8": 0.5}, "head_covered": {"median_w": 40.5, "median_h": 31.2, "p10_w": 27.1, "p10_h": 17.6, "pct_short_lt16": 5.5, "pct_short_lt8": 0.0}, "head_uncovered": {"median_w": 44.2, "median_h": 38.4, "p10_w": 29.1, "p10_h": 25.4, "pct_short_lt16": 0.2, "pct_short_lt8": 0.0}, "mask_on": {"median_w": 28.7, "median_h": 23.1, "p10_w": 14.3, "p10_h": 13.4, "pct_short_lt16": 25.0, "pct_short_lt8": 2.1}, "mask_off": {"median_w": 26.7, "median_h": 22.6, "p10_w": 12.9, "p10_h": 11.4, "pct_short_lt16": 29.5, "pct_short_lt8": 4.4}, "apron": {"median_w": 49.9, "median_h": 121.4, "p10_w": 34.5, "p10_h": 83.9, "pct_short_lt16": 0.2, "pct_short_lt8": 0.0}, "no_apron": {"median_w": 49.0, "median_h": 100.0, "p10_w": 26.9, "p10_h": 70.8, "pct_short_lt16": 1.2, "pct_short_lt8": 0.0}, "hand_bare": {"median_w": 20.2, "median_h": 18.6, "p10_w": 14.0, "p10_h": 11.7, "pct_short_lt16": 43.2, "pct_short_lt8": 0.6}, "hand_gloved": {"median_w": 24.1, "median_h": 20.8, "p10_w": 15.1, "p10_h": 12.4, "pct_short_lt16": 30.1, "pct_short_lt8": 1.4}, "rte_food": {"median_w": 23.3, "median_h": 18.6, "p10_w": 12.7, "p10_h": 10.0, "pct_short_lt16": 40.4, "pct_short_lt8": 4.9}, "raw_meat": {"median_w": 10.0, "median_h": 16.5, "p10_w": 7.6, "p10_h": 9.4, "pct_short_lt16": 92.6, "pct_short_lt8": 16.8}, "smoking": {"median_w": 7.7, "median_h": 8.0, "p10_w": 4.3, "p10_h": 4.6, "pct_short_lt16": 98.9, "pct_short_lt8": 69.4}, "phone": {"median_w": 20.7, "median_h": 17.1, "p10_w": 13.3, "p10_h": 9.8, "pct_short_lt16": 53.9, "pct_short_lt8": 5.6}, "bin_closed": {"median_w": 25.2, "median_h": 66.9, "p10_w": 23.6, "p10_h": 63.2, "pct_short_lt16": 0.5, "pct_short_lt8": 0.0}, "bin_open": {"median_w": 60.4, "median_h": 98.5, "p10_w": 45.2, "p10_h": 80.7, "pct_short_lt16": 0.7, "pct_short_lt8": 0.7}} | PASS |
| val: frames | 280 | 280 | PASS |
| val: boxes | 2381 | 2381 | PASS |
| val: background_frames | 3 | 3 | PASS |
| val: boxes of person (id 0) | 377 | 377 | PASS |
| val: boxes of head_covered (id 1) | 141 | 141 | PASS |
| val: boxes of head_uncovered (id 2) | 217 | 217 | PASS |
| val: boxes of mask_on (id 3) | 89 | 89 | PASS |
| val: boxes of mask_off (id 4) | 129 | 129 | PASS |
| val: boxes of apron (id 5) | 178 | 178 | PASS |
| val: boxes of no_apron (id 6) | 122 | 122 | PASS |
| val: boxes of hand_bare (id 7) | 165 | 165 | PASS |
| val: boxes of hand_gloved (id 8) | 261 | 261 | PASS |
| val: boxes of rte_food (id 9) | 529 | 529 | PASS |
| val: boxes of raw_meat (id 10) | 10 | 10 | PASS |
| val: boxes of smoking (id 11) | 12 | 12 | PASS |
| val: boxes of phone (id 12) | 23 | 23 | PASS |
| val: boxes of bin_closed (id 13) | 95 | 95 | PASS |
| val: boxes of bin_open (id 14) | 33 | 33 | PASS |
| val: frames containing person | 267 | 267 | PASS |
| val: frames containing head_covered | 141 | 141 | PASS |
| val: frames containing head_uncovered | 217 | 217 | PASS |
| val: frames containing mask_on | 88 | 88 | PASS |
| val: frames containing mask_off | 129 | 129 | PASS |
| val: frames containing apron | 142 | 142 | PASS |
| val: frames containing no_apron | 122 | 122 | PASS |
| val: frames containing hand_bare | 115 | 115 | PASS |
| val: frames containing hand_gloved | 180 | 180 | PASS |
| val: frames containing rte_food | 184 | 184 | PASS |
| val: frames containing raw_meat | 10 | 10 | PASS |
| val: frames containing smoking | 11 | 11 | PASS |
| val: frames containing phone | 23 | 23 | PASS |
| val: frames containing bin_closed | 95 | 95 | PASS |
| val: frames containing bin_open | 33 | 33 | PASS |
| val: frames_per_camera | {"camera 1": 235, "camera 2": 45} | {"camera 1": 235, "camera 2": 45} | PASS |
| val: frames_per_clip | {"20261106230458823": 59, "20261106232021417": 62, "20261109215956846": 37, "20261109220241282": 31, "20261109220450926": 20, "20261109220937414": 34, "20261109222354933": 26, "20261109223111084": 11} | {"20261106230458823": 59, "20261106232021417": 62, "20261109215956846": 37, "20261109220241282": 31, "20261109220450926": 20, "20261109220937414": 34, "20261109222354933": 26, "20261109223111084": 11} | PASS |
| val: frames_per_recording_date | {"2026-11-06": 121, "2026-11-09": 159} | {"2026-11-06": 121, "2026-11-09": 159} | PASS |
| val: frames_per_annotator | {"Basima Maham": 54, "Soban Khan": 37, "Tahreem Fatima": 189} | {"Basima Maham": 54, "Soban Khan": 37, "Tahreem Fatima": 189} | PASS |
| val: resolutions | {"2688x1520": 280} | {"2688x1520": 280} | PASS |
| val: hazard frames (A: annotator tags, B: violation classes in label files) | {"H-01": 217, "H-03": 11, "H-05": 33, "H-06": 122, "H-08": 129} | {"H-01": 217, "H-03": 11, "H-05": 33, "H-06": 122, "H-08": 129} | PASS |
| val: rule-engine input frames H-02/H-04/H-07 | {"H-02": 74, "H-04": 23, "H-07": 10} | {"H-02": 74, "H-04": 23, "H-07": 10} | PASS |
| val: box size at 640 per class | {"person": {"median_w": 107.6, "median_h": 180.2, "p10_w": 68.9, "p10_h": 114.6, "pct_short_lt16": 4.2, "pct_short_lt8": 4.2}, "head_covered": {"median_w": 43.2, "median_h": 36.0, "p10_w": 26.2, "p10_h": 20.6, "pct_short_lt16": 0.7, "pct_short_lt8": 0.0}, "head_uncovered": {"median_w": 46.2, "median_h": 42.6, "p10_w": 25.7, "p10_h": 22.2, "pct_short_lt16": 0.5, "pct_short_lt8": 0.0}, "mask_on": {"median_w": 29.9, "median_h": 21.1, "p10_w": 9.8, "p10_h": 9.3, "pct_short_lt16": 40.4, "pct_short_lt8": 10.1}, "mask_off": {"median_w": 24.7, "median_h": 16.0, "p10_w": 15.5, "p10_h": 11.9, "pct_short_lt16": 53.5, "pct_short_lt8": 0.8}, "apron": {"median_w": 53.6, "median_h": 122.2, "p10_w": 31.2, "p10_h": 73.5, "pct_short_lt16": 0.0, "pct_short_lt8": 0.0}, "no_apron": {"median_w": 56.4, "median_h": 110.4, "p10_w": 30.3, "p10_h": 74.8, "pct_short_lt16": 0.0, "pct_short_lt8": 0.0}, "hand_bare": {"median_w": 20.5, "median_h": 19.6, "p10_w": 12.4, "p10_h": 10.7, "pct_short_lt16": 47.3, "pct_short_lt8": 0.6}, "hand_gloved": {"median_w": 27.6, "median_h": 23.3, "p10_w": 19.5, "p10_h": 15.0, "pct_short_lt16": 14.9, "pct_short_lt8": 0.0}, "rte_food": {"median_w": 18.1, "median_h": 13.6, "p10_w": 16.7, "p10_h": 11.4, "pct_short_lt16": 68.4, "pct_short_lt8": 0.0}, "raw_meat": {"median_w": 12.3, "median_h": 18.3, "p10_w": 10.0, "p10_h": 11.0, "pct_short_lt16": 100.0, "pct_short_lt8": 0.0}, "smoking": {"median_w": 4.6, "median_h": 4.5, "p10_w": 3.1, "p10_h": 2.2, "pct_short_lt16": 100.0, "pct_short_lt8": 100.0}, "phone": {"median_w": 17.2, "median_h": 11.3, "p10_w": 13.7, "p10_h": 8.9, "pct_short_lt16": 78.3, "pct_short_lt8": 4.3}, "bin_closed": {"median_w": 36.4, "median_h": 77.6, "p10_w": 23.3, "p10_h": 49.9, "pct_short_lt16": 0.0, "pct_short_lt8": 0.0}, "bin_open": {"median_w": 50.9, "median_h": 102.7, "p10_w": 34.8, "p10_h": 81.2, "pct_short_lt16": 0.0, "pct_short_lt8": 0.0}} | {"person": {"median_w": 107.6, "median_h": 180.2, "p10_w": 68.9, "p10_h": 114.6, "pct_short_lt16": 4.2, "pct_short_lt8": 4.2}, "head_covered": {"median_w": 43.2, "median_h": 36.0, "p10_w": 26.2, "p10_h": 20.6, "pct_short_lt16": 0.7, "pct_short_lt8": 0.0}, "head_uncovered": {"median_w": 46.2, "median_h": 42.6, "p10_w": 25.7, "p10_h": 22.2, "pct_short_lt16": 0.5, "pct_short_lt8": 0.0}, "mask_on": {"median_w": 29.9, "median_h": 21.1, "p10_w": 9.8, "p10_h": 9.3, "pct_short_lt16": 40.4, "pct_short_lt8": 10.1}, "mask_off": {"median_w": 24.7, "median_h": 16.0, "p10_w": 15.5, "p10_h": 11.9, "pct_short_lt16": 53.5, "pct_short_lt8": 0.8}, "apron": {"median_w": 53.6, "median_h": 122.2, "p10_w": 31.2, "p10_h": 73.5, "pct_short_lt16": 0.0, "pct_short_lt8": 0.0}, "no_apron": {"median_w": 56.4, "median_h": 110.4, "p10_w": 30.3, "p10_h": 74.8, "pct_short_lt16": 0.0, "pct_short_lt8": 0.0}, "hand_bare": {"median_w": 20.5, "median_h": 19.6, "p10_w": 12.4, "p10_h": 10.7, "pct_short_lt16": 47.3, "pct_short_lt8": 0.6}, "hand_gloved": {"median_w": 27.6, "median_h": 23.3, "p10_w": 19.5, "p10_h": 15.0, "pct_short_lt16": 14.9, "pct_short_lt8": 0.0}, "rte_food": {"median_w": 18.1, "median_h": 13.6, "p10_w": 16.7, "p10_h": 11.4, "pct_short_lt16": 68.4, "pct_short_lt8": 0.0}, "raw_meat": {"median_w": 12.3, "median_h": 18.3, "p10_w": 10.0, "p10_h": 11.0, "pct_short_lt16": 100.0, "pct_short_lt8": 0.0}, "smoking": {"median_w": 4.6, "median_h": 4.5, "p10_w": 3.1, "p10_h": 2.2, "pct_short_lt16": 100.0, "pct_short_lt8": 100.0}, "phone": {"median_w": 17.2, "median_h": 11.3, "p10_w": 13.7, "p10_h": 8.9, "pct_short_lt16": 78.3, "pct_short_lt8": 4.3}, "bin_closed": {"median_w": 36.4, "median_h": 77.6, "p10_w": 23.3, "p10_h": 49.9, "pct_short_lt16": 0.0, "pct_short_lt8": 0.0}, "bin_open": {"median_w": 50.9, "median_h": 102.7, "p10_w": 34.8, "p10_h": 81.2, "pct_short_lt16": 0.0, "pct_short_lt8": 0.0}} | PASS |
| train frames + val frames == total frames | 1864 | 1864 | PASS |
| train boxes + val boxes == total boxes | 16677 | 16677 | PASS |
| sum of boxes per class == total boxes | 16677 | 16677 | PASS |
| sum of label lines == sum of index.csv num_boxes | 16677 | 16677 | PASS |
| no video clip in both train and val | 0 | 0 | PASS |
| val clips == approved list | ["20261106230458823", "20261106232021417", "20261109215956846", "20261109220241282", "20261109220450926", "20261109220937414", "20261109222354933", "20261109223111084"] | ["20261106230458823", "20261106232021417", "20261109215956846", "20261109220241282", "20261109220450926", "20261109220937414", "20261109222354933", "20261109223111084"] | PASS |
| every frame_id contains a clip id | 0 | 0 | PASS |
| no near-duplicate cluster (phash <= 4, audit) in both train and val | 0 | 0 | PASS |
| every dataset image is a byte-identical copy of the raw image in the source export/ZIP | 0 | 0 | PASS |
| renumbering: person -> id 0 (source name count - removed + renamed in == label lines) | 2472 | 2472 | PASS |
| renumbering: head_covered -> id 1 (source name count - removed + renamed in == label lines) | 847 | 847 | PASS |
| renumbering: head_uncovered -> id 2 (source name count - removed + renamed in == label lines) | 1585 | 1585 | PASS |
| renumbering: mask_on -> id 3 (source name count - removed + renamed in == label lines) | 818 | 818 | PASS |
| renumbering: mask_off -> id 4 (source name count - removed + renamed in == label lines) | 977 | 977 | PASS |
| renumbering: apron -> id 5 (source name count - removed + renamed in == label lines) | 765 | 765 | PASS |
| renumbering: no_apron -> id 6 (source name count - removed + renamed in == label lines) | 951 | 951 | PASS |
| renumbering: hand_bare -> id 7 (source name count - removed + renamed in == label lines) | 1727 | 1727 | PASS |
| renumbering: hand_gloved -> id 8 (source name count - removed + renamed in == label lines) | 1369 | 1369 | PASS |
| renumbering: rte_food -> id 9 (source name count - removed + renamed in == label lines) | 3395 | 3395 | PASS |
| renumbering: raw_meat -> id 10 (source name count - removed + renamed in == label lines) | 105 | 105 | PASS |
| renumbering: smoking -> id 11 (source name count - removed + renamed in == label lines) | 192 | 192 | PASS |
| renumbering: phone -> id 12 (source name count - removed + renamed in == label lines) | 327 | 327 | PASS |
| renumbering: bin_closed -> id 13 (source name count - removed + renamed in == label lines) | 980 | 980 | PASS |
| renumbering: bin_open -> id 14 (source name count - removed + renamed in == label lines) | 167 | 167 | PASS |
| no source class name left unmapped | {} | {} | PASS |

## How to train

On the training laptop, first change the `path:` line in `dataset/data.yaml` to where the `dataset` folder is on that machine. Then:

```
yolo detect train data=<path>/RADS_YOLO_prepared/dataset/data.yaml model=yolo26s.pt imgsz=640 epochs=100 batch=4 amp=False device=0 workers=2
```

`amp=False` keeps training in FP32. If the GTX 1650 (4 GB) runs out of memory, lower `batch`.

