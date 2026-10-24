"""Split dataset/raw menjadi dataset/images|labels/{train,val} (YOLO layout)."""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

import yaml

from common import DATASET, ensure_dirs


def main() -> int:
    ap = argparse.ArgumentParser(description="Split dataset raw -> train/val")
    ap.add_argument("--raw", default=str(DATASET / "raw"))
    ap.add_argument("--out", default=str(DATASET))
    ap.add_argument("--val", type=float, default=0.2, help="fraksi data validasi")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--classes", default="", help="daftar kelas dipisah koma (default: raw/classes.txt)")
    ap.add_argument("--keep-empty", action="store_true", help="ikutkan gambar tanpa label sebagai negatif")
    args = ap.parse_args()

    raw = Path(args.raw)
    out = Path(args.out)
    kelas_file = raw / "classes.txt"
    if args.classes:
        classes = [c.strip() for c in args.classes.split(",") if c.strip()]
    elif kelas_file.exists():
        classes = [c.strip() for c in kelas_file.read_text(encoding="utf-8").splitlines() if c.strip()]
    else:
        raise SystemExit(f"{kelas_file} tidak ada; jalankan labeling dulu atau pakai --classes")
    images = sorted((raw / "images").glob("*.jpg"))
    if not images:
        print(f"[split] tidak ada gambar di {raw / 'images'}", file=sys.stderr)
        return 1

    pairs = []
    skipped = 0
    for img in images:
        lbl = raw / "labels" / f"{img.stem}.txt"
        if lbl.exists() and lbl.read_text(encoding="utf-8").strip():
            pairs.append((img, lbl))
        elif args.keep_empty and (not lbl.exists() or not lbl.read_text(encoding="utf-8").strip()):
            pairs.append((img, None))
        else:
            skipped += 1
    if not pairs:
        print("[split] tidak ada pasangan gambar+label valid", file=sys.stderr)
        return 1

    random.Random(args.seed).shuffle(pairs)
    n_val = max(1, int(round(len(pairs) * args.val))) if len(pairs) >= 2 else 0
    n_val = min(n_val, len(pairs) - 1) if len(pairs) >= 2 else 0
    val_set = set(range(n_val))

    for split in ("train", "val"):
        ensure_dirs(out / "images" / split, out / "labels" / split)
        for old in (out / "images" / split).glob("*"):
            old.unlink()
        for old in (out / "labels" / split).glob("*"):
            old.unlink()

    counts = {"train": 0, "val": 0}
    for i, (img, lbl) in enumerate(pairs):
        split = "val" if i in val_set else "train"
        dst_lbl = out / "labels" / split / f"{img.stem}.txt"
        shutil.copy2(img, out / "images" / split / img.name)
        if lbl is None:
            dst_lbl.write_text("", encoding="utf-8")
        else:
            shutil.copy2(lbl, dst_lbl)
        counts[split] += 1

    names = {i: n for i, n in enumerate(classes)}
    data = {
        "path": str(out.resolve()),
        "train": "images/train",
        "val": "images/val",
        "names": names,
    }
    (out / "classes.txt").write_text("\n".join(classes) + "\n", encoding="utf-8")
    (out / "data.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    print(f"[split] kelas={classes}")
    print(f"[split] total={len(pairs)} train={counts['train']} val={counts['val']} dilewati={skipped}")
    print(f"[split] config: {out / 'data.yaml'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
