from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
VAL = ROOT / "dataset" / "images" / "val"
LBL = ROOT / "dataset" / "labels" / "val"
OUT = ROOT / "build" / "demo_ballpoint_box.mp4"
W, H, FPS, HOLD = 1280, 720, 25, 30
CLS = 1


def letterbox(img):
    h, w = img.shape[:2]
    s = min(W / w, H / h)
    img = cv2.resize(img, (int(w * s), int(h * s)))
    canvas = np.zeros((H, W, 3), np.uint8)
    y = (H - img.shape[0]) // 2
    x = (W - img.shape[1]) // 2
    canvas[y:y + img.shape[0], x:x + img.shape[1]] = img
    return canvas


imgs = []
for img_path in sorted(VAL.glob("*.jpg")):
    lbl = LBL / (img_path.stem + ".txt")
    if not lbl.exists():
        continue
    ids = {int(line.split()[0]) for line in lbl.read_text().splitlines() if line.strip()}
    if CLS in ids:
        imgs.append(img_path)

model = YOLO(str(ROOT / "weights" / "multiclass_best.pt"))
scored = []
for p in imgs:
    r = model.predict(str(p), conf=0.15, imgsz=640, verbose=False)[0]
    conf = 0.0
    for box in r.boxes:
        if int(box.cls[0]) == CLS:
            conf = max(conf, float(box.conf[0]))
    scored.append((conf, p))
scored.sort(reverse=True)
chosen = [p for c, p in scored[:6] if c > 0]
print("ballpoint val conf:", [round(c, 2) for c, _ in scored])

writer = cv2.VideoWriter(str(OUT), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
for f in chosen:
    img = letterbox(cv2.imread(str(f)))
    cv2.putText(img, "ballpoint_box (val)", (20, 40), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, (0, 255, 255), 2, cv2.LINE_AA)
    for _ in range(HOLD):
        writer.write(img)
writer.release()
print(f"ok: {OUT} ({len(chosen)} gambar)")
