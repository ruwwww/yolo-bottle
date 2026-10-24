import random
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
VAL = ROOT / "dataset" / "images" / "val"
LBL = ROOT / "dataset" / "labels" / "val"
NAMES = ["bottle", "ballpoint_box", "computer_mouse"]
W, H, FPS, HOLD = 1280, 720, 25, 30


def letterbox(img):
    h, w = img.shape[:2]
    s = min(W / w, H / h)
    img = cv2.resize(img, (int(w * s), int(h * s)))
    canvas = np.zeros((H, W, 3), np.uint8)
    y = (H - img.shape[0]) // 2
    x = (W - img.shape[1]) // 2
    canvas[y:y + img.shape[0], x:x + img.shape[1]] = img
    return canvas


groups = {i: [] for i in range(len(NAMES))}
for img_path in sorted(VAL.glob("*.jpg")):
    lbl = LBL / (img_path.stem + ".txt")
    if not lbl.exists():
        continue
    ids = {int(line.split()[0]) for line in lbl.read_text().splitlines() if line.strip()}
    for i in ids:
        groups[i].append(img_path)

random.seed(0)
for i, name in enumerate(NAMES):
    files = groups[i][:]
    random.shuffle(files)
    chosen = files[:6]
    out = ROOT / "build" / f"demo_{name}.mp4"
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for f in chosen:
        img = letterbox(cv2.imread(str(f)))
        cv2.putText(img, f"{name} (val)", (20, 40), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (0, 255, 255), 2, cv2.LINE_AA)
        for _ in range(HOLD):
            writer.write(img)
    writer.release()
    print(f"ok: {out} ({len(chosen)} gambar)")
