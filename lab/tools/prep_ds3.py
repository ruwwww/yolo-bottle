"""Round 2: dataset2 + hard negative COCO128 (tanpa gambar berlabel botol)."""
from __future__ import annotations

import glob
import random
import shutil
from pathlib import Path

from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "dataset2"
OUT = ROOT / "dataset3"
HOLD = ROOT / "build" / "coco_hold"
PICS = ROOT / "build" / "picsum"
random.seed(1)

if OUT.exists():
    shutil.rmtree(OUT)
for sub in ("images/train", "images/val", "labels/train", "labels/val"):
    (OUT / sub).mkdir(parents=True, exist_ok=True)

for split in ("train", "val"):
    for sub in ("images", "labels"):
        for f in (SRC / sub / split).iterdir():
            shutil.copy2(f, OUT / sub / split / f.name)

coco = sorted((ROOT / "build/coco/coco128/images/train2017").glob("*.jpg"))


def bottles(f: Path) -> bool:
    lab = Path(str(f).replace("images", "labels")).with_suffix(".txt")
    return lab.exists() and any(line.split()[0] == "39" for line in lab.read_text().splitlines() if line.strip())


coco = [f for f in coco if not bottles(f)]

model = YOLO(str(ROOT / "weights" / "ets_v2.pt"))
fp_imgs = []
for f in coco:
    r = model.predict(str(f), conf=0.5, imgsz=640, device="cpu", verbose=False)[0]
    if r.boxes is not None and len(r.boxes):
        fp_imgs.append(f)
print(f"[ds3] coco {len(coco)} gambar, FP saat ini {len(fp_imgs)}")

train_coco = fp_imgs + [f for f in coco if f not in fp_imgs][: max(0, 80 - len(fp_imgs))]
train_coco = train_coco[:80]
rest = [f for f in coco if f not in train_coco]
val_coco, hold_coco = rest[:10], rest[10:]
print(f"[ds3] coco train={len(train_coco)} val={len(val_coco)} holdout={len(hold_coco)}")


def emit(files, split: str, prefix: str) -> None:
    for i, f in enumerate(files):
        shutil.copy2(f, OUT / "images" / split / f"{prefix}_{i:04d}.jpg")
        (OUT / "labels" / split / f"{prefix}_{i:04d}.txt").write_text("", encoding="utf-8")


emit(train_coco, "train", "coco")
emit(val_coco, "val", "coco")

write_hold = HOLD
if write_hold.exists():
    shutil.rmtree(write_hold)
write_hold.mkdir(parents=True)
for i, f in enumerate(hold_coco):
    shutil.copy2(f, write_hold / f"coco_{i:03d}.jpg")

from urllib.request import urlopen

for i in range(20):
    dst = write_hold / f"picsum_{i:03d}.jpg"
    with urlopen(f"https://picsum.photos/seed/hold{i}/480/360", timeout=25) as r:
        dst.write_bytes(r.read())

for f in sorted((ROOT / "build/negatives").glob("*.jpg"))[:8]:
    shutil.copy2(f, write_hold / f"inpaint_{f.name}")

print(f"[ds3] train={len(list((OUT / 'images/train').iterdir()))} val={len(list((OUT / 'images/val').iterdir()))}")
print(f"[ds3] holdout={len(list(write_hold.glob('*.jpg')))} gambar")
