import glob
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from md_remote import MoondreamEngine  # noqa: E402

URL = "http://100.91.215.116:8090"
ETS = Path(r"C:\Users\darlis\Downloads\ETS\dataset")
HOLD = Path("build/coco_hold")
PROMPTS = ["bottle", "water bottle", "drink bottle", "plastic bottle", "minuman botol"]


def gt_boxes(img_path: Path):
    lab = Path(str(img_path).replace("\\images\\", "\\labels\\")).with_suffix(".txt")
    if not lab.exists():
        return []
    img = cv2.imread(str(img_path))
    h, w = img.shape[:2]
    out = []
    for line in lab.read_text().splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        cx, cy, bw, bh = map(float, parts[1:5])
        out.append(((cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h))
    return out


def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if not inter:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / max(1e-6, ua)


def main():
    pos = sorted(list((ETS / "val" / "images").glob("*.jpg")) + list((ETS / "test" / "images").glob("*.jpg")))
    neg = sorted(HOLD.glob("*.jpg"))
    print(f"positif {len(pos)} gambar, negatif {len(neg)} gambar")
    for prompt in PROMPTS:
        eng = MoondreamEngine(URL, prompt=prompt, label="air_mineral")
        if not eng.health():
            print("server mati", eng.last_error)
            return 1
        tot_gt = 0
        hit = 0
        det_total = 0
        tps = []
        ms = []
        for p in pos:
            frame = cv2.imread(str(p))
            gts = gt_boxes(p)
            dets = eng.detect(frame)
            ms.append(eng.last_ms)
            tot_gt += len(gts)
            det_total += len(dets)
            used = set()
            for d in dets:
                best, bi = 0.0, -1
                for i, g in enumerate(gts):
                    if i in used:
                        continue
                    v = iou(d[:4], g)
                    if v > best:
                        best, bi = v, i
                if best >= 0.5:
                    used.add(bi)
                    hit += 1
        fp_imgs = 0
        fp_det = 0
        fp_ms = []
        for p in neg:
            frame = cv2.imread(str(p))
            dets = eng.detect(frame)
            fp_ms.append(eng.last_ms)
            if dets:
                fp_imgs += 1
                fp_det += len(dets)
        print(
            f"{prompt:20s} recall {hit}/{tot_gt} ({hit/max(1,tot_gt):.2f}) "
            f"det={det_total} | neg FP {fp_imgs}/{len(neg)} img, {fp_det} kotak | "
            f"{sum(ms)/len(ms):.0f}ms/{sum(fp_ms)/len(fp_ms):.0f}ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
