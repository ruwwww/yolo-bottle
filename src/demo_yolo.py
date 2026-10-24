"""Demo deteksi botol realtime dengan weight hasil training.

Contoh:
    python src/demo_yolo.py
    python src/demo_yolo.py --weights weights/bottle_best.pt --conf 0.4
    python src/demo_yolo.py --source gambar.jpg --no-window
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from pathlib import Path

import cv2

from common import WEIGHTS, letterbox, open_capture, screen_size


def main() -> int:
    ap = argparse.ArgumentParser(description="Demo YOLO deteksi botol")
    ap.add_argument("--weights", default=str(WEIGHTS / "bottle_best.pt"))
    ap.add_argument("--source", default="0")
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--save", default="", help="rekam hasil ke file .mp4")
    ap.add_argument("--save-dir", default="", help="simpan hasil gambar/video ke folder ini")
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--fullscreen", action="store_true", help="tampilkan layar penuh (letterbox)")
    ap.add_argument("--max-frames", type=int, default=0)
    args = ap.parse_args()

    weights = Path(args.weights)
    if not weights.exists():
        print(f"[demo] weight tidak ditemukan: {weights}", file=sys.stderr)
        print("[demo] jalankan training dulu: python src/train_remote.py", file=sys.stderr)
        return 1

    from ultralytics import YOLO

    model = YOLO(str(weights))
    names = model.names
    source = int(args.source) if str(args.source).isdigit() else args.source
    cap = open_capture(source)
    if isinstance(source, int):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    save_dir = Path(args.save_dir) if args.save_dir else None
    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
    is_image = isinstance(source, str) and Path(source).suffix.lower() in {
        ".jpg", ".jpeg", ".png", ".bmp", ".webp"
    }

    writer = None
    disp = None
    if args.fullscreen and not args.no_window:
        disp = screen_size()
        cv2.namedWindow("demo deteksi botol", cv2.WINDOW_NORMAL)
        cv2.setWindowProperty("demo deteksi botol", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        print(f"[demo] fullscreen {disp[0]}x{disp[1]}")

    if args.save:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        w = disp[0] if disp else int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
        h = disp[1] if disp else int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720
        writer = cv2.VideoWriter(args.save, fourcc, fps, (w, h))

    frames = 0
    fps_hist = deque(maxlen=30)
    last = time.time()
    print("[demo] q/ESC keluar")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames += 1
            result = model.predict(frame, conf=args.conf, imgsz=args.imgsz, verbose=False)[0]
            count = 0
            if result.boxes is not None:
                xyxy = result.boxes.xyxy.cpu().numpy()
                confs = result.boxes.conf.cpu().numpy()
                clss = result.boxes.cls.cpu().numpy().astype(int)
                count = len(xyxy)
                for (x1, y1, x2, y2), conf, cls in zip(xyxy, confs, clss):
                    p1, p2 = (int(x1), int(y1)), (int(x2), int(y2))
                    label = names.get(int(cls), str(cls)) if isinstance(names, dict) else names[int(cls)]
                    color = (0, 200, 90)
                    cv2.rectangle(frame, p1, p2, color, 2)
                    txt = f"{label} {conf:.2f}"
                    (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                    cv2.rectangle(frame, (p1[0], p1[1] - th - 10), (p1[0] + tw + 8, p1[1]), color, -1)
                    cv2.putText(frame, txt, (p1[0] + 4, p1[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (10, 20, 10), 2)

            now = time.time()
            dt = now - last
            last = now
            if dt > 0:
                fps_hist.append(1.0 / dt)
            fps = sum(fps_hist) / len(fps_hist) if fps_hist else 0.0
            cv2.putText(
                frame,
                f"{count} botol | {fps:.1f} FPS",
                (16, 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 180),
                2,
                cv2.LINE_AA,
            )

            if save_dir is not None and is_image:
                cv2.imwrite(
                    str(save_dir / f"deteksi_{Path(str(source)).stem}.jpg"),
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 92],
                )
            tampil = letterbox(frame, disp) if disp else frame
            if writer is not None:
                writer.write(tampil)
            if not args.no_window:
                cv2.imshow("demo deteksi botol", tampil)
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break
            if args.max_frames and frames >= args.max_frames:
                break
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()

    print(f"[demo] {frames} frame diproses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
