import time
from collections import defaultdict
from pathlib import Path

from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
VAL = ROOT / "dataset" / "images" / "val"
LBL = ROOT / "dataset" / "labels" / "val"
NAMES = ["bottle", "ballpoint_box", "computer_mouse"]


def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


def xywh2xyxy(b):
    x, y, w, h = b
    return x - w / 2, y - h / 2, x + w / 2, y + h / 2


model = YOLO(str(ROOT / "weights" / "multiclass_best.pt"))
tp = defaultdict(int)
fp = defaultdict(int)
fn = defaultdict(int)
n_img = 0
t0 = None
for img in sorted(VAL.glob("*.jpg")):
    lbl = LBL / (img.stem + ".txt")
    gt = []
    if lbl.exists():
        for line in lbl.read_text().splitlines():
            p = line.split()
            if len(p) >= 5:
                gt.append((int(p[0]), xywh2xyxy([float(v) for v in p[1:5]])))
    r = model.predict(str(img), conf=0.35, imgsz=640, verbose=False)[0]
    if t0 is None:
        t0 = time.perf_counter()
    preds = []
    for box in r.boxes:
        c = int(box.cls[0])
        preds.append((c, xywh2xyxy(box.xywhn[0].tolist())))
    used = set()
    for c, g in gt:
        best, best_j = 0.0, -1
        for j, (pc, pb) in enumerate(preds):
            if j in used or pc != c:
                continue
            v = iou(g, pb)
            if v > best:
                best, best_j = v, j
        if best >= 0.5:
            tp[c] += 1
            used.add(best_j)
        else:
            fn[c] += 1
    for j, (pc, _) in enumerate(preds):
        if j not in used:
            fp[pc] += 1
    n_img += 1

dt = time.perf_counter() - t0
print(f"val={n_img} imgs, {n_img / dt:.1f} pred/s (CPU)")
for c, name in enumerate(NAMES):
    p = tp[c] / (tp[c] + fp[c]) if tp[c] + fp[c] else 0
    r = tp[c] / (tp[c] + fn[c]) if tp[c] + fn[c] else 0
    print(f"{name:15s} TP={tp[c]:3d} FP={fp[c]:3d} FN={fn[c]:3d}  P={p:.3f} R={r:.3f}")
