#!/usr/bin/env python3
"""Phase 1 step 1: inventory All_Yolo_Data (read-only) and extract every ZIP
(including ZIPs nested inside ZIPs / extracted folders) into _work/extracted/.

Usage: python 01_inventory_extract.py <source_dir> <prepared_dir>
"""
import sys, os, zipfile, csv
from pathlib import Path

SRC = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
EXTRACT = OUT / "_work" / "extracted"
AUDIT = OUT / "audit"
EXTRACT.mkdir(parents=True, exist_ok=True)
AUDIT.mkdir(parents=True, exist_ok=True)

files_rows, zip_rows, problems = [], [], []
_dest_owner = {}


def unique_dest(name, zpath):
    """One folder per zip; add a suffix if two different zips share a name."""
    d = EXTRACT / name; i = 2
    while d in _dest_owner and _dest_owner[d] != zpath:
        d = EXTRACT / f"{name}__{i}"; i += 1
    _dest_owner[d] = zpath
    return d


def safe_extract(zpath, dest):
    with zipfile.ZipFile(zpath) as zf:
        bad = zf.testzip()
        if bad:
            raise zipfile.BadZipFile(f"CRC error in member {bad}")
        for m in zf.infolist():
            if not str((dest / m.filename).resolve()).startswith(str(dest.resolve())):
                raise RuntimeError(f"unsafe path in zip: {m.filename}")
        if (dest / ".extract_done").exists():
            return "already_extracted"
        zf.extractall(dest)
        (dest / ".extract_done").write_text(str(zpath))
        return "extracted"


def walk(root, origin):
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath).resolve()
        # never scan our own output folder as input (except the extracted trees we recurse into explicitly)
        if (dp == OUT or OUT in dp.parents) and not (dp == root or root in dp.parents):
            dirnames[:] = []
            continue
        dirnames.sort()
        for fn in sorted(filenames):
            if fn == ".extract_done":
                continue
            p = dp / fn
            try:
                size = p.stat().st_size
            except OSError as e:
                problems.append(("unreadable_file", str(p), str(e))); continue
            files_rows.append({"origin": origin, "path": str(p), "size_bytes": size})
            if fn.lower().endswith(".zip"):
                dest = unique_dest(p.stem, p)
                try:
                    status = safe_extract(p, dest)
                    zip_rows.append({"zip": str(p), "size_bytes": size, "extracted_to": str(dest), "status": status})
                    walk(dest.resolve(), origin=f"zip:{p.name}")
                except Exception as e:
                    zip_rows.append({"zip": str(p), "size_bytes": size, "extracted_to": "", "status": f"ERROR {e}"})
                    problems.append(("corrupt_zip", str(p), str(e)))


walk(SRC, origin="source")

with open(AUDIT / "inventory_files.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["origin", "path", "size_bytes"]); w.writeheader(); w.writerows(files_rows)
with open(AUDIT / "inventory_zips.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["zip", "size_bytes", "extracted_to", "status"]); w.writeheader(); w.writerows(zip_rows)
with open(AUDIT / "inventory_problems.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["kind", "path", "detail"]); w.writerows(problems)

print(f"files recorded: {len(files_rows)}  zips: {len(zip_rows)}  problems: {len(problems)}")
for z in zip_rows:
    print(z["status"], z["zip"])
