#!/usr/bin/env python3
"""Phase 1 step 2: load every frame of every export into one table.

Sources: every export root found in <source_dir> (excluding the prepared dir) and in
<prepared_dir>/_work/extracted. An export root is a folder containing annotations.json,
frames/ (new layout) or images/ (old layout).
annotations.json is the source of truth; per-frame metadata files are the fallback
(also used for frame folders that annotations.json does not list).

Outputs (in <prepared_dir>/_work/):
  exports.csv     one row per export
  frames_all.csv  one row per (export, frame entry)
  boxes_all.csv   one row per box (keyed by row_uid of frames_all)
  load_problems.csv
  image_stats.csv one row per distinct raw image path (real size, brightness, std, phash, sha1)

Usage: python 02_load_frames.py <source_dir> <prepared_dir>
"""
import sys, os, re, json, hashlib, csv
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import pandas as pd

SRC = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
WORK = OUT / "_work"
EXTRACT = WORK / "extracted"

IMG_EXT = (".jpg", ".jpeg", ".png")
problems = []  # (kind, export, frame_id, detail)


def find_export_roots():
    roots = []
    for base in (SRC, EXTRACT):
        for dirpath, dirnames, filenames in os.walk(base):
            dp = Path(dirpath).resolve()
            if base == SRC and (dp == OUT or OUT in dp.parents):
                dirnames[:] = []; continue
            if "annotations.json" in filenames or "frames" in dirnames or \
               ("images" in dirnames and ("labels" in dirnames or "metadata" in dirnames)):
                roots.append(dp)
                dirnames[:] = []  # an export does not contain other exports (nested zips were extracted separately)
            dirnames.sort()
    return sorted(set(roots))


def part_of(name):
    m = re.search(r"part(\d+)of(\d+)", name)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def first(*vals):
    for v in vals:
        if v not in (None, ""):
            return v
    return None


def box_rows(boxes, w, h):
    out = []
    for i, b in enumerate(boxes or []):
        yolo = b.get("bbox_yolo")
        px = b.get("bbox_xywh_px")
        if px is None and b.get("bbox_xyxy_px"):
            x1, y1, x2, y2 = b["bbox_xyxy_px"]; px = [x1, y1, x2 - x1, y2 - y1]
        out.append({
            "box_idx": i,
            "class_name": b.get("class_name"),
            "src_class_id": b.get("class_id"),
            "cx": yolo[0] if yolo else None, "cy": yolo[1] if yolo else None,
            "bw": yolo[2] if yolo else None, "bh": yolo[3] if yolo else None,
            "px_x": px[0] if px else None, "px_y": px[1] if px else None,
            "px_w": px[2] if px else None, "px_h": px[3] if px else None,
            "status": b.get("status"), "hazard": b.get("hazard"),
            "worker_id": b.get("worker_id"), "occluded": b.get("occluded"), "truncated": b.get("truncated"),
            "box_annotator": b.get("annotator"),
        })
    return out


def load_export(root: Path):
    ann_path = root / "annotations.json"
    ann = None
    if ann_path.exists():
        try:
            ann = json.load(open(ann_path))
        except Exception as e:
            problems.append(("unreadable_annotations_json", str(root), "", str(e)))
    layout = "frames" if (root / "frames").is_dir() else ("images" if (root / "images").is_dir() else "unknown")
    pnum, ptot = part_of(root.name)
    exp = {
        "export": root.name, "path": str(root), "layout": layout,
        "exported_by": ann.get("exported_by") if ann else None,
        "exported_at": ann.get("exported_at") if ann else None,
        "schema_version": ann.get("schema_version") if ann else None,
        "part": pnum, "parts_total": ptot,
        "frames_in_annotations_json": len(ann["frames"]) if ann else None,
    }
    class_names_by_id = {c["id"]: c["name"] for c in ann.get("classes", [])} if ann else {}
    frames, boxes = [], []

    def add(rec, bxs):
        rec["row_uid"] = f"{root.name}::{rec['frame_id']}::{len(frames)}"
        frames.append(rec)
        for b in bxs:
            b["row_uid"] = rec["row_uid"]; b["export"] = root.name; b["frame_id"] = rec["frame_id"]
            boxes.append(b)

    listed = set()
    if ann:
        for f in ann.get("frames", []):
            fid = f.get("frame_id")
            listed.add(fid)
            src, img, rec_, ctx, an = (f.get(k) or {} for k in ("source", "image", "recording", "context", "annotation"))
            raw = root / f["image_raw"] if f.get("image_raw") else None
            annotated = root / f["image_annotated"] if f.get("image_annotated") else None
            w, h = img.get("width"), img.get("height")
            add({
                "frame_id": fid, "export": root.name, "source_of_record": "annotations.json",
                "annotator": an.get("annotator"), "last_edited_by": an.get("last_edited_by"),
                "updated_at": an.get("updated_at"), "exported_at": exp["exported_at"],
                "review_status": an.get("review_status"),
                "raw_path": str(raw) if raw else None, "annotated_path": str(annotated) if annotated else None,
                "meta_width": w, "meta_height": h,
                "restaurant_id": rec_.get("restaurant_id"), "session_id": rec_.get("session_id"),
                "camera_view": rec_.get("camera_view"), "camera_id": rec_.get("camera_id"),
                "recording_date": rec_.get("recording_date"), "scenario_type": rec_.get("scenario_type"),
                "clip_id": src.get("clip_id"), "clip_file": src.get("clip_file"),
                "frame_index": src.get("frame_index"), "timestamp_sec": src.get("timestamp_sec"), "fps": src.get("fps"),
                "clip_duration_sec": src.get("clip_duration_sec"),
                "split": ctx.get("split"), "hazards_marked": "|".join(ctx.get("hazards_marked") or []),
                "hazards_from_boxes": "|".join(ctx.get("hazards_from_boxes") or []),
                "is_background": ctx.get("is_background"), "num_boxes": len(f.get("boxes") or []),
            }, box_rows(f.get("boxes"), w, h))

    # fallback / extra frames: per-frame metadata (new layout)
    if layout == "frames":
        for d in sorted((root / "frames").iterdir()):
            if not d.is_dir():
                continue
            fid = d.name
            if fid in listed:
                continue
            mp = d / f"{fid}_metadata.json"
            if ann:
                problems.append(("frame_folder_not_in_annotations_json", root.name, fid, "loaded from metadata file"))
            try:
                m = json.load(open(mp))
            except Exception as e:
                problems.append(("unreadable_metadata_json", root.name, fid, str(e))); continue
            im, g, s = m.get("image", {}), m.get("group", {}), m.get("source", {})
            sc = m.get("scenario", {})
            add({
                "frame_id": m.get("frame_id", fid), "export": root.name, "source_of_record": "metadata.json",
                "annotator": m.get("annotator"), "last_edited_by": None, "updated_at": None,
                "exported_at": exp["exported_at"], "review_status": None,
                "raw_path": str(d / im["raw"]) if im.get("raw") else None,
                "annotated_path": str(d / im["annotated"]) if im.get("annotated") else None,
                "meta_width": im.get("width"), "meta_height": im.get("height"),
                "restaurant_id": g.get("restaurant_id"), "session_id": g.get("session_id"),
                "camera_view": g.get("camera_view"), "camera_id": g.get("camera_id"),
                "recording_date": sc.get("recording_date"), "scenario_type": sc.get("scenario_type"),
                "clip_id": None, "clip_file": g.get("clip_file"),
                "frame_index": s.get("frame_index"), "timestamp_sec": s.get("timestamp_sec"), "fps": s.get("fps"),
                "clip_duration_sec": None,
                "split": m.get("split"), "hazards_marked": "|".join(sc.get("hazards_marked") or []),
                "hazards_from_boxes": "|".join(sc.get("hazards_from_boxes") or []),
                "is_background": m.get("is_background"), "num_boxes": m.get("num_boxes"),
            }, box_rows(m.get("boxes"), im.get("width"), im.get("height")))

    # old layout: images/raw, images/annotated, labels, metadata (match by file stem)
    if layout == "images":
        raw_dir = root / "images" / "raw"
        for p in sorted(raw_dir.glob("*")) if raw_dir.is_dir() else []:
            if p.suffix.lower() not in IMG_EXT:
                continue
            fid = p.stem[:-4] if p.stem.endswith("_raw") else p.stem
            if fid in listed:
                continue
            mp = root / "metadata" / f"{fid}.json"
            m = {}
            if mp.exists():
                try: m = json.load(open(mp))
                except Exception as e: problems.append(("unreadable_metadata_json", root.name, fid, str(e)))
            bxs = m.get("boxes")
            if bxs is None:
                lp = root / "labels" / f"{fid}.txt"
                bxs = []
                if lp.exists():
                    for line in lp.read_text().split("\n"):
                        parts = line.split()
                        if len(parts) == 5:
                            cid = int(parts[0])
                            bxs.append({"class_id": cid, "class_name": class_names_by_id.get(cid, f"UNKNOWN_ID_{cid}"),
                                        "bbox_yolo": [float(x) for x in parts[1:]]})
                    problems.append(("old_layout_labels_txt_used", root.name, fid, "class names from annotations.json/classes.txt ids"))
            im, g, s = m.get("image", {}), m.get("group", {}), m.get("source", {})
            ann_img = root / "images" / "annotated" / p.name.replace("_raw", "_annotated")
            add({
                "frame_id": fid, "export": root.name, "source_of_record": "old_layout",
                "annotator": m.get("annotator"), "last_edited_by": None, "updated_at": None,
                "exported_at": exp["exported_at"], "review_status": None,
                "raw_path": str(p), "annotated_path": str(ann_img) if ann_img.exists() else None,
                "meta_width": im.get("width"), "meta_height": im.get("height"),
                "restaurant_id": g.get("restaurant_id"), "session_id": g.get("session_id"),
                "camera_view": g.get("camera_view"), "camera_id": g.get("camera_id"),
                "recording_date": None, "scenario_type": None, "clip_id": None, "clip_file": g.get("clip_file"),
                "frame_index": s.get("frame_index"), "timestamp_sec": s.get("timestamp_sec"), "fps": s.get("fps"),
                "clip_duration_sec": None, "split": m.get("split"),
                "hazards_marked": "", "hazards_from_boxes": "", "is_background": m.get("is_background"),
                "num_boxes": len(bxs),
            }, box_rows(bxs, im.get("width"), im.get("height")))

    exp["frames_loaded"] = len(frames)
    exp["frame_folders"] = sum(1 for d in (root / "frames").iterdir() if d.is_dir()) if layout == "frames" else None
    return exp, frames, boxes


def image_stats(path):
    import numpy as np
    from PIL import Image
    import imagehash
    r = {"raw_path": path, "exists": os.path.exists(path), "readable": False, "real_width": None, "real_height": None,
         "mean_brightness": None, "std_brightness": None, "phash": None, "sha1": None, "file_size": None, "error": ""}
    if not r["exists"]:
        return r
    try:
        data = open(path, "rb").read()
        r["sha1"] = hashlib.sha1(data).hexdigest(); r["file_size"] = len(data)
        with Image.open(path) as im:
            im.load()
            r["real_width"], r["real_height"] = im.size
            g = im.convert("L")
            a = np.asarray(g.resize((g.width // 4, g.height // 4)), dtype=np.float32)
            r["mean_brightness"] = float(a.mean()); r["std_brightness"] = float(a.std())
            r["phash"] = str(imagehash.phash(im))
        r["readable"] = True
    except Exception as e:
        r["error"] = str(e)
    return r


def main():
    roots = find_export_roots()
    exps, frames, boxes = [], [], []
    for r in roots:
        e, f, b = load_export(r)
        exps.append(e); frames += f; boxes += b
        print(f"{e['export']}: layout={e['layout']} by={e['exported_by']} frames={e['frames_loaded']}")
    fdf = pd.DataFrame(frames); bdf = pd.DataFrame(boxes); edf = pd.DataFrame(exps)

    # image stats (cached)
    cache_p = WORK / "image_stats.csv"
    cache = pd.read_csv(cache_p) if cache_p.exists() else pd.DataFrame(columns=["raw_path"])
    todo = sorted(set(fdf["raw_path"].dropna()) - set(cache["raw_path"]))
    print(f"reading {len(todo)} images...")
    with ProcessPoolExecutor() as ex:
        new = list(ex.map(image_stats, todo, chunksize=8))
    stats = pd.concat([cache, pd.DataFrame(new)], ignore_index=True) if new else cache
    stats.to_csv(cache_p, index=False)

    edf.to_csv(WORK / "exports.csv", index=False)
    fdf.to_csv(WORK / "frames_all.csv", index=False)
    bdf.to_csv(WORK / "boxes_all.csv", index=False)
    pd.DataFrame(problems, columns=["kind", "export", "frame_id", "detail"]).to_csv(WORK / "load_problems.csv", index=False)
    print(f"exports={len(edf)} frame rows={len(fdf)} unique frame_ids={fdf.frame_id.nunique()} boxes={len(bdf)} problems={len(problems)}")


if __name__ == "__main__":
    main()
