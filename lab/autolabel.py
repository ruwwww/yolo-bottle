"""Auto-labeling bbox botol dari webcam memakai detector COCO pretrained.

Setiap frame yang mengandung botol disimpan (maksimal N gambar) bersama label
YOLO-format (.txt) hasil deteksi pretrained, jadi dataset siap dilatih.

Contoh:
    python src/autolabel.py --source 0 --max 50
    python src/autolabel.py --source video.mp4 --no-window --max 50
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2

from common import (
    BOTTLE_COCO_ID,
    DATASET,
    RAW_IMAGES,
    RAW_LABELS,
    add_root_to_path,
    ensure_dirs,
    open_capture,
)

add_root_to_path()


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def boxes_to_labels(xyxy, frame_w: int, frame_h: int, min_area: float):
    """Convert absolute xyxy boxes to normalized YOLO lines, filtered by area."""
    labels = []
    for x1, y1, x2, y2 in xyxy:
        x1, x2 = sorted((clamp01(x1 / frame_w), clamp01(x2 / frame_w)))
        y1, y2 = sorted((clamp01(y1 / frame_h), clamp01(y2 / frame_h)))
        w, h = x2 - x1, y2 - y1
        if w <= 0 or h <= 0:
            continue
        if w * h < min_area:
            continue
        xc, yc = x1 + w / 2, y1 + h / 2
        labels.append(f"0 {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}")
    return labels


def draw(frame, xyxy, confs):
    for (x1, y1, x2, y2), conf in zip(xyxy, confs):
        p1 = (int(x1), int(y1))
        p2 = (int(x2), int(y2))
        cv2.rectangle(frame, p1, p2, (0, 220, 90), 2)
        cv2.putText(
            frame,
            f"bottle {conf:.2f}",
            (p1[0], max(18, p1[1] - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 220, 90),
            2,
            cv2.LINE_AA,
        )
    return frame


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-label botol via webcam (COCO pretrained)")
    ap.add_argument("--source", default="0", help="index webcam, path video, atau folder gambar")
    ap.add_argument("--model", default="yolo11n.pt", help="model pretrained COCO")
    ap.add_argument("--conf", type=float, default=0.5, help="threshold confidence")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--max", type=int, default=50, help="maksimal gambar disimpan")
    ap.add_argument("--cooldown", type=float, default=1.0, help="jeda minimal antar simpan (detik)")
    ap.add_argument("--min-area", type=float, default=0.003, help="area bbox minimum (fraksi frame)")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--out", default=str(DATASET / "raw"), help="folder output dataset raw")
    ap.add_argument("--no-window", action="store_true", help="headless (tanpa jendela preview)")
    ap.add_argument("--max-frames", type=int, default=0, help="berhenti setelah N frame (0 = tanpa batas)")
    ap.add_argument("--keep-running", action="store_true", help="tetap jalan walau max gambar tercapai")
    args = ap.parse_args()

    out = Path(args.out)
    images_dir = out / "images"
    labels_dir = out / "labels"
    preview_dir = out / "preview"
    ensure_dirs(images_dir, labels_dir, preview_dir)

    from ultralytics import YOLO

    model = YOLO(args.model)
    source = int(args.source) if str(args.source).isdigit() else args.source
    cap = open_capture(source)
    if isinstance(source, int):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    meta_path = out / "metadata.csv"
    new_meta = not meta_path.exists()
    meta_fh = meta_path.open("a", newline="", encoding="utf-8")
    writer = csv.writer(meta_fh)
    if new_meta:
        writer.writerow(["file", "timestamp", "n_boxes", "boxes", "conf_mean"])

    saved = 0
    frames = 0
    last_save = 0.0
    paused = False
    last_status = ""

    print(f"[autolabel] source={source} max={args.max} out={images_dir}")
    print("[autolabel] tombol: q/ESC keluar | s simpan manual | spasi pause")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[autolabel] frame habis / kamera terputus")
                break
            frames += 1
            frame_h, frame_w = frame.shape[:2]

            result = model.predict(
                frame,
                conf=args.conf,
                imgsz=args.imgsz,
                classes=[BOTTLE_COCO_ID],
                verbose=False,
            )[0]
            xyxy, confs = [], []
            if result.boxes is not None and len(result.boxes):
                xyxy = result.boxes.xyxy.cpu().numpy().tolist()
                confs = result.boxes.conf.cpu().numpy().tolist()

            labels = boxes_to_labels(xyxy, frame_w, frame_h, args.min_area)
            view = frame.copy()
            draw(view, xyxy, confs)

            now = time.time()
            can_save = bool(labels) and (now - last_save) >= args.cooldown
            reached_max = saved >= args.max

            if reached_max and not args.keep_running:
                last_status = f"MAX {args.max} gambar tercapai - berhenti"
                if not args.no_window:
                    cv2.putText(view, last_status, (16, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 255), 2)
                    cv2.imshow("auto-label botol", view)
                    cv2.waitKey(300)
                break

            if can_save and not paused and not reached_max:
                stamp = datetime.now()
                name = f"bottle_{stamp:%Y%m%d_%H%M%S_%f}"[:-3]
                img_path = images_dir / f"{name}.jpg"
                lbl_path = labels_dir / f"{name}.txt"
                cv2.imwrite(str(img_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                lbl_path.write_text("\n".join(labels) + "\n", encoding="utf-8")
                cv2.imwrite(str(preview_dir / f"{name}.jpg"), view, [cv2.IMWRITE_JPEG_QUALITY, 80])
                writer.writerow(
                    [
                        img_path.name,
                        stamp.isoformat(timespec="seconds"),
                        len(labels),
                        ";".join(labels),
                        f"{sum(confs) / len(confs):.4f}" if confs else "",
                    ]
                )
                meta_fh.flush()
                saved += 1
                last_save = now
                print(f"[autolabel] saved {saved}/{args.max}  {img_path.name}  boxes={len(labels)}")
            elif paused and args.no_window:
                cv2.waitKey(50)

            if not args.no_window:
                hud = f"tersimpan {saved}/{args.max}  |  botol: {len(labels)}  |  frame {frames}"
                if paused:
                    hud += "  [PAUSED]"
                cv2.putText(view, hud, (16, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
                cv2.putText(
                    view,
                    "q=keluar  s=simpan  spasi=pause",
                    (16, frame_h - 16),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (200, 200, 200),
                    1,
                    cv2.LINE_AA,
                )
                cv2.imshow("auto-label botol", view)
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    break
                if key == ord(" "):
                    paused = not paused
                    print(f"[autolabel] {'PAUSED' if paused else 'LANJUT'}")
                if key == ord("s") and labels:
                    last_save = 0.0

            if args.max_frames and frames >= args.max_frames:
                print(f"[autolabel] batas {args.max_frames} frame tercapai")
                break
    finally:
        cap.release()
        meta_fh.close()
        if not args.no_window:
            cv2.destroyAllWindows()

    print(f"[autolabel] selesai: {saved} gambar, {frames} frame diproses")
    if saved:
        print(f"[autolabel] berikutnya: python src/split_dataset.py")
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
