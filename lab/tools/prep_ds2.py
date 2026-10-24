"""Bangun dataset2 (1 kelas air_mineral) + negatif keras untuk menekan false positive."""
from __future__ import annotations

import glob
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

import cv2
import numpy as np
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
ETS = Path(r"C:\Users\darlis\Downloads\ETS")
OUT = ROOT / "dataset2"
NEG_TEST = ROOT / "build" / "negtest"
CROPS = ROOT / "build" / "crops"
CROPS.mkdir(parents=True, exist_ok=True)
PICSUM = ROOT / "build" / "picsum"
random.seed(0)
PICSUM.mkdir(parents=True, exist_ok=True)
NEG_TEST.mkdir(parents=True, exist_ok=True)

for sub in ("images/train", "images/val", "labels/train", "labels/val"):
    (OUT / sub).mkdir(parents=True, exist_ok=True)


def download(seed: str) -> Path:
    dst = PICSUM / f"picsum_{seed}.jpg"
    if not dst.exists():
        with urlopen(f"https://picsum.photos/seed/{seed}/480/360", timeout=25) as r:
            dst.write_bytes(r.read())
    return dst


with ThreadPoolExecutor(8) as ex:
    list(ex.map(download, [f"neg{i}" for i in range(40)]))

model = YOLO(str(ROOT / "weights" / "ets_best.pt"))
pos: list[tuple[Path, list[str]]] = []
raw = sorted((ETS / "foto_mentah" / "air_mineral").glob("*.jpg"))
for p in raw:
    img = cv2.imread(str(p))
    h, w = img.shape[:2]
    r = model.predict(str(p), conf=0.25, imgsz=640, device="cpu", verbose=False)[0]
    lines = []
    if r.boxes is not None:
        for b in r.boxes.xyxy.tolist():
            x1, y1, x2, y2 = (max(0.0, b[0]), max(0.0, b[1]), min(w, b[2]), min(h, b[3]))
            bw, bh = (x2 - x1) / w, (y2 - y1) / h
            area, ar = bw * bh, bw / max(bh, 1e-6)
            if area < 0.004 or area > 0.8 or not (0.12 <= ar <= 5.0):
                continue
            lines.append(f"0 {(x1 + x2) / 2 / w:.6f} {(y1 + y2) / 2 / h:.6f} {bw:.6f} {bh:.6f}")
    if lines:
        pos.append((p, lines))
print(f"[prep] positif {len(pos)}/{len(raw)} gambar")

random.shuffle(pos)
split = int(len(pos) * 0.85)

# negatif: crop latar dari foto (tanpa botol), picsum, inpaint, pelanggaran, aset ultralytics
crops: list[Path] = []
for p, lines in pos:
    img = cv2.imread(str(p))
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    for line in lines:
        _, xc, yc, bw, bh = map(float, line.split())
        x1, y1 = int((xc - bw / 2) * w), int((yc - bh / 2) * h)
        x2, y2 = int((xc + bw / 2) * w), int((yc + bh / 2) * h)
        mask[y1:y2, x1:x2] = 255
    mask = cv2.dilate(mask, np.ones((25, 25), np.uint8))
    size = int(0.5 * min(h, w))
    if size < 120:
        continue
    best = None
    for _ in range(30):
        x = random.randint(0, w - size)
        y = random.randint(0, h - size)
        cov = mask[y : y + size, x : x + size].mean() / 255
        if best is None or cov < best[0]:
            best = (cov, x, y)
        if cov < 0.02:
            break
    cov, x, y = best
    if cov < 0.02:
        dst = CROPS / f"crop_{p.stem}.jpg"
        cv2.imwrite(str(dst), cv2.resize(img[y : y + size, x : x + size], (480, 360)), [cv2.IMWRITE_JPEG_QUALITY, 88])
        crops.append(dst)

random.shuffle(crops)
crops = crops[:60]
pics = sorted(PICSUM.glob("*.jpg"))
inpaint = sorted((ROOT / "build" / "negatives").glob("*.jpg"))
viol = sorted((ETS / "pelanggaran").glob("*.jpg"))
assets = sorted((ROOT / ".venv" / "Lib" / "site-packages" / "ultralytics" / "assets").glob("*.jpg"))

holdout = [(p, None) for p in inpaint[:8] + pics[:8] + assets[:2] + viol]
train_neg = [(p, None) for p in crops + pics[8:30] + inpaint[8:16] + assets[2:] + viol]
val_neg = [(p, None) for p in pics[30:] + inpaint[16:]]
print(f"[prep] negatif train={len(train_neg)} val={len(val_neg)} holdout={len(holdout)}")


def emit(items, split_name: str, prefix: str) -> None:
    for i, (p, lines) in enumerate(items):
        dst = OUT / "images" / split_name / f"{prefix}_{i:04d}.jpg"
        img = cv2.imread(str(p))
        if img is None:
            continue
        cv2.imwrite(str(dst), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        lab = OUT / "labels" / split_name / f"{prefix}_{i:04d}.txt"
        lab.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


emit(pos[:split], "train", "pos")
emit(pos[split:], "val", "pos")
emit(train_neg, "train", "neg")
emit(val_neg, "val", "neg")

for i, (p, _) in enumerate(holdout):
    img = cv2.imread(str(p))
    if img is not None:
        cv2.imwrite(str(NEG_TEST / f"hold_{i:03d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 88])

print(f"[prep] dataset2: train={len(pos[:split]) + len(train_neg)} val={len(pos[split:]) + len(val_neg)}")
print(f"[prep] negtest: {len(list(NEG_TEST.glob('*.jpg')))} gambar")
