"""Label dataset capture di server GPU dengan Moondream (text-guided bbox).

Dijalankan DI SERVER (conda env `ai`), bukan di laptop. Model dimuat sekali,
lalu setiap gambar di root/<kelas>/ dideteksi dengan prompt kelas tersebut.

Input:
    capture/classes.txt        urutan kelas = class-id YOLO
    capture/prompts.txt        "kelas=prompt" per baris (opsional)
    capture/<kelas>/*.jpg

Output:
    out/images/*.jpg           gambar (hanya yang punya kotak, kecuali --keep-empty)
    out/labels/*.txt           YOLO "cls cx cy w h" (boleh kosong = negatif)
    out/classes.txt
    out/metadata.csv

Contoh:
    python label_moondream.py --root capture --out raw --resize 640
    python label_moondream.py --root capture --out raw --limit 5 --skip-empty
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

from PIL import Image

import moondream as md

FIELDS = ["file", "kelas", "boxes", "prompt", "ms"]


def parse_prompts(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in text.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


def read_prompts(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Label capture dengan Moondream (GPU)")
    ap.add_argument("--root", default="capture")
    ap.add_argument("--out", default="raw")
    ap.add_argument("--prompts", default="", help='override "kelas=prompt,kelas2=prompt2"')
    ap.add_argument(
        "--only", default="",
        help="label hanya kelas ini (id tetap mengikuti classes.txt)",
    )
    ap.add_argument("--model", default="moondream2")
    ap.add_argument("--resize", type=int, default=640, help="sisi maks sebelum deteksi")
    ap.add_argument("--min-area", type=float, default=0.004, help="area kotak minimum")
    ap.add_argument("--limit", type=int, default=0, help="maks gambar per kelas (uji cepat)")
    ap.add_argument("--skip-empty", action="store_true", help="buang gambar tanpa objek")
    ap.add_argument("--max-aspect", type=float, default=6.0)
    args = ap.parse_args()

    root = Path(args.root)
    out = Path(args.out)
    classes_file = root / "classes.txt"
    if not classes_file.exists():
        raise SystemExit(f"{classes_file} tidak ada")
    classes = [c.strip() for c in classes_file.read_text(encoding="utf-8").splitlines() if c.strip()]
    only = {c.strip() for c in args.only.split(",") if c.strip()}
    prompts = {c: c.replace("_", " ") for c in classes}
    prompts.update(read_prompts(root / "prompts.txt"))
    prompts.update(parse_prompts(args.prompts))

    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    (out / "classes.txt").write_text("\n".join(classes) + "\n", encoding="utf-8")

    print(f"[label] model {args.model} memuat...", flush=True)
    t0 = time.time()
    model = md.photon(args.model)
    print(f"[label] siap ({time.time() - t0:.1f}s), kelas={classes}", flush=True)

    meta = (out / "metadata.csv").open("w", newline="", encoding="utf-8")
    writer = csv.writer(meta)
    writer.writerow(FIELDS)

    total = 0
    berisi = 0
    kosong = 0
    per_kelas: Counter = Counter()
    for cls_id, kelas in enumerate(classes):
        if only and kelas not in only:
            continue
        folder = root / kelas
        if not folder.exists():
            print(f"[label] (lewati) folder {folder} tidak ada")
            continue
        imgs = sorted(folder.glob("*.jpg")) + sorted(folder.glob("*.png"))
        if args.limit:
            imgs = imgs[: args.limit]
        prompt = prompts.get(kelas) or kelas.replace("_", " ")
        print(f"[label] {kelas}: {len(imgs)} gambar, prompt '{prompt}'", flush=True)
        for n, path in enumerate(imgs, 1):
            try:
                im = Image.open(path).convert("RGB")
            except Exception as exc:  # noqa: BLE001
                print(f"[label]   gagal buka {path.name}: {exc}")
                continue
            if args.resize and max(im.size) > args.resize:
                s = args.resize / max(im.size)
                im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
            t = time.time()
            try:
                objs = model.detect(im, prompt).get("objects", [])
            except Exception as exc:  # noqa: BLE001
                print(f"[label]   error {path.name}: {exc}")
                continue
            ms = (time.time() - t) * 1000
            lines = []
            for o in objs:
                x1, y1, x2, y2 = o["x_min"], o["y_min"], o["x_max"], o["y_max"]
                bw, bh = x2 - x1, y2 - y1
                if bw <= 0 or bh <= 0 or bw * bh < args.min_area:
                    continue
                if bw / max(bh, 1e-6) > args.max_aspect:
                    continue
                lines.append(
                    f"{cls_id} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {bw:.6f} {bh:.6f}"
                )
            stem = path.stem
            if (out / "images" / f"{stem}.jpg").exists():
                stem = f"{kelas}__{stem}"
            if not lines and args.skip_empty:
                continue
            im.save(out / "images" / f"{stem}.jpg", quality=95)
            (out / "labels" / f"{stem}.txt").write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
            )
            writer.writerow([f"{stem}.jpg", kelas, len(lines), prompt, f"{ms:.0f}"])
            meta.flush()
            total += 1
            per_kelas[kelas] += 1
            if lines:
                berisi += 1
            else:
                kosong += 1
            if n % 20 == 0 or n == len(imgs):
                print(f"[label]   {kelas} {n}/{len(imgs)}", flush=True)

    meta.close()
    rincian = ", ".join(f"{k}={v}" for k, v in per_kelas.items()) or "-"
    print(
        f"[label] selesai: {total} gambar ({berisi} berlabel, {kosong} negatif), "
        f"{rincian} -> {out}",
        flush=True,
    )
    return 0 if total or kosong else 1


if __name__ == "__main__":
    sys.exit(main())
