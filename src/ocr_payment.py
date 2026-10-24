"""Demo OCR nominal pembayaran ("Rp X.XXX") dari layar HP lewat webcam.

- ROI di tengah frame di-crop, di-upscale, lalu di-OCR (PaddleOCR).
- Nominal divalidasi dengan voting beberapa frame sebelum dinyatakan terverifikasi.
- Snapshot + log CSV disimpan otomatis saat nominal terverifikasi.

Contoh:
    python src/ocr_payment.py
    python src/ocr_payment.py --roi 0.25,0.25,0.5,0.5 --every 5
    python src/ocr_payment.py --image foto_layar.jpg --no-window
    python src/ocr_payment.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from common import ROOT, ensure_dirs, fmt_rupiah, open_capture

MIN_AMOUNT = 100
MAX_AMOUNT = 1_000_000_000

LETTER_MAP = str.maketrans(
    {
        "O": "0", "o": "0", "Q": "0",
        "I": "1", "l": "1", "i": "1", "|": "1", "!": "1",
        "S": "5", "s": "5",
        "B": "8",
        "Z": "2", "z": "2",
    }
)

RP_RE = re.compile(r"(?i)(?:rp|idr)\s*[.:]?\s*([0-9oilsbgz.,\s]{1,24})")
NUM_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?)(?!\d)")


def parse_amount(token: str | None) -> int | None:
    """Parse nominal Indonesia: 'Rp 250.000', '1.234.567', 'Rp25.000,00' -> int."""
    if token is None:
        return None
    t = re.sub(r"\s+", "", str(token))
    if not any(c.isdigit() for c in t):
        return None
    t = t.translate(LETTER_MAP)
    t = re.sub(r"[^0-9.,]", "", t).strip(".,")
    if not t or not any(c.isdigit() for c in t):
        return None

    has_dot, has_comma = "." in t, "," in t
    value: float | None = None
    try:
        if has_dot and has_comma:
            dec = max(t.rfind("."), t.rfind(","))
            left = re.sub(r"[.,]", "", t[:dec])
            right = re.sub(r"[.,]", "", t[dec + 1:])
            if not left:
                return None
            value = float(left + "." + (right or "0"))
        elif not has_dot and not has_comma:
            value = float(t)
        else:
            parts = [p for p in t.split("." if has_dot else ",") if p]
            if not parts:
                return None
            if len(parts) == 1:
                value = float(parts[0])
            elif len(parts) > 2:
                value = float("".join(parts))
            else:
                left, right = parts
                if len(right) == 3 and 0 < len(left) <= 3:
                    value = float(left + right)
                elif len(right) in (1, 2) and left:
                    value = float(left + "." + right)
                else:
                    value = float(left + right)
    except ValueError:
        return None

    if value is None:
        return None
    amount = int(value)  # Rupiah tanpa sen: buang pecahan
    if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
        return None
    return amount


def find_amounts(texts: list[str]) -> list[int]:
    """Cari nominal di kumpulan teks OCR, utamakan yang berlabel Rp/IDR."""
    joined = " ".join(t for t in texts if t)
    found: list[int] = []
    for match in RP_RE.finditer(joined):
        amount = parse_amount(match.group(1))
        if amount is not None and amount not in found:
            found.append(amount)
    if not found:
        for match in NUM_RE.finditer(joined):
            amount = parse_amount(match.group(0))
            if amount is not None and amount not in found:
                found.append(amount)
    return found


def parse_roi(value: str, w: int, h: int) -> tuple[int, int, int, int]:
    parts = [float(p) for p in value.split(",")]
    if len(parts) != 4:
        raise ValueError("--roi harus x,y,w,h")
    if all(0.0 <= p <= 1.0 for p in parts):
        x, y, rw, rh = int(parts[0] * w), int(parts[1] * h), int(parts[2] * w), int(parts[3] * h)
    else:
        x, y, rw, rh = (int(p) for p in parts)
    x = max(0, min(x, w - 1))
    y = max(0, min(y, h - 1))
    rw = max(8, min(rw, w - x))
    rh = max(8, min(rh, h - y))
    return x, y, rw, rh


def preprocess(roi: np.ndarray, mode: str = "clahe") -> tuple[np.ndarray, float]:
    h, w = roi.shape[:2]
    scale = max(1.0, 1000.0 / max(w, 1))
    up = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    if mode == "clahe":
        gray = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
        gray = cv2.bilateralFilter(gray, 5, 50, 50)
    elif mode == "binary":
        gray = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
        gray = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 12
        )
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), scale


def _norm_items(raw) -> list[tuple[str, float, object | None]]:
    """Normalisasi output PaddleOCR v2/v3 -> [(text, score, poly)]."""
    items: list[tuple[str, float, object | None]] = []
    if not raw:
        return items
    for page in raw:
        if isinstance(page, dict) or hasattr(page, "get"):
            texts = page.get("rec_texts") or []
            scores = page.get("rec_scores") or []
            polys = page.get("rec_polys")
            if polys is None:
                polys = page.get("dt_polys") or []
            for i, text in enumerate(texts):
                score = float(scores[i]) if i < len(scores) else 1.0
                poly = polys[i] if i < len(polys) else None
                items.append((str(text), score, poly))
        elif isinstance(page, (list, tuple)):
            for line in page:
                try:
                    poly, payload = line[0], line[1]
                    text, score = payload[0], float(payload[1])
                    items.append((str(text), score, poly))
                except (TypeError, IndexError, ValueError):
                    continue
    return items


class PaddleEngine:
    def __init__(self, lang: str = "latin"):
        # Di Windows, torch harus di-import sebelum paddle/paddleocr (konflik DLL MKL).
        try:
            import torch  # noqa: F401
        except ImportError:
            pass
        from paddleocr import PaddleOCR

        self.ocr = None
        last_err: Exception | None = None
        # PaddleOCR 3.x: matikan mkldnn (bug oneDNN di Windows + PaddlePaddle 3.x).
        candidates = [
            dict(
                lang=lang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False,
            ),
            dict(lang=lang, use_textline_orientation=False, enable_mkldnn=False),
            dict(lang=lang, use_angle_cls=False, enable_mkldnn=False),
            dict(lang=lang),
        ]
        for kwargs in candidates:
            try:
                self.ocr = PaddleOCR(**kwargs)
                print(f"[ocr] PaddleOCR siap (lang={kwargs.get('lang')})")
                break
            except Exception as exc:  # noqa: BLE001 - API beda antar versi
                last_err = exc
        if self.ocr is None:
            raise RuntimeError(f"PaddleOCR gagal init: {last_err}")

    def read(self, image: np.ndarray) -> list[tuple[str, float, object | None]]:
        ocr = self.ocr
        if ocr is None:
            raise RuntimeError("PaddleOCR belum terinisialisasi")
        if hasattr(ocr, "predict"):
            try:
                return _norm_items(ocr.predict(image))
            except Exception:  # noqa: BLE001
                pass
        try:
            return _norm_items(ocr.ocr(image, cls=False))
        except TypeError:
            return _norm_items(ocr.ocr(image))


def bbox_of(poly) -> tuple[float, float, float, float] | None:
    try:
        arr = np.asarray(poly, dtype=float).reshape(-1, 2)
        return float(arr[:, 0].min()), float(arr[:, 1].min()), float(arr[:, 0].max()), float(arr[:, 1].max())
    except Exception:  # noqa: BLE001
        return None


def draw_panel(frame, roi, amount: int | None, confirmed: int | None, status: str, debug: bool):
    x, y, w, h = roi
    color = (0, 220, 120) if confirmed else (0, 200, 255)
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
    for cx in range(x, x + w, 36):
        cv2.line(frame, (cx, y), (min(cx + 18, x + w), y), color, 2)
        cv2.line(frame, (cx, y + h), (min(cx + 18, x + w), y + h), color, 2)
    cv2.putText(frame, "layar HP", (x, max(18, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)

    bar_h = 92
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (frame.shape[1], bar_h), (15, 15, 15), -1)
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)

    if confirmed is not None:
        text = fmt_rupiah(confirmed)
        cv2.putText(frame, text, (18, 54), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 255, 140), 3, cv2.LINE_AA)
        cv2.putText(frame, "TERVERIFIKASI", (20, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 140), 2, cv2.LINE_AA)
    elif amount is not None:
        text = fmt_rupiah(amount)
        cv2.putText(frame, text, (18, 54), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 3, cv2.LINE_AA)
        cv2.putText(frame, "membaca...", (20, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 180, 180), 1, cv2.LINE_AA)
    else:
        cv2.putText(frame, "Arahkan layar HP ke kotak", (18, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(frame, "menunggu nominal Rp ...", (18, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 180, 180), 1, cv2.LINE_AA)

    cv2.putText(frame, status, (frame.shape[1] - 420, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (170, 170, 170), 1, cv2.LINE_AA)
    if debug:
        cv2.putText(
            frame,
            "q keluar | s snapshot | b mode preprocess | d debug | r reset",
            (18, frame.shape[0] - 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
    return frame


def draw_ocr_boxes(frame, roi, items, scale: float):
    x0, y0, _, _ = roi
    for _, score, poly in items:
        box = bbox_of(poly)
        if not box:
            continue
        x1, y1, x2, y2 = box
        p1 = (int(x0 + x1 / scale), int(y0 + y1 / scale))
        p2 = (int(x0 + x2 / scale), int(y0 + y2 / scale))
        cv2.rectangle(frame, p1, p2, (255, 120, 0), 1)


def save_snapshot(frame, amount: int, folder: Path, writer, log_fh) -> Path:
    ensure_dirs(folder)
    stamp = datetime.now()
    path = folder / f"bayar_{stamp:%Y%m%d_%H%M%S}_{amount}.jpg"
    cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    writer.writerow([stamp.isoformat(timespec="seconds"), amount, path.name])
    log_fh.flush()
    return path


def run_image(engine, args) -> int:
    img = cv2.imread(args.image)
    if img is None:
        print(f"[ocr] gagal baca {args.image}", file=sys.stderr)
        return 1
    h, w = img.shape[:2]
    roi = parse_roi(args.roi or "0,0,1,1", w, h)
    x, y, rw, rh = roi
    crop, scale = preprocess(img[y:y + rh, x:x + rw], args.mode)
    items = engine.read(crop)
    texts = [t for t, _, _ in items]
    amounts = find_amounts(texts)
    print("[ocr] teks:", " | ".join(texts) if texts else "(kosong)")
    print("[ocr] nominal:", [fmt_rupiah(a) for a in amounts] or "tidak terdeteksi")
    amount = amounts[0] if amounts else None
    frame = draw_panel(img.copy(), roi, amount, amount, f"{len(items)} teks", True)
    draw_ocr_boxes(frame, roi, items, scale)
    if args.out:
        out_dir = Path(args.out)
        ensure_dirs(out_dir)
        path = out_dir / f"ocr_{Path(args.image).stem}.jpg"
        cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        print(f"[ocr] disimpan: {path}")
    if not args.no_window:
        cv2.imshow("OCR pembayaran", frame)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    return 0 if amounts else 2


def selftest() -> int:
    cases = {
        "Rp 250.000": 250000,
        "Rp. 1.234.567": 1234567,
        "IDR 50,000": 50000,
        "Rp 25.000,00": 25000,
        "total Rp 5.000": 5000,
        "Rp 100000": 100000,
        "Rp 1.234.567,89": 1234567,
        "Rp1.500": 1500,
        "bukan uang": None,
        "2024": None,
    }
    failed = 0
    for text, expected in cases.items():
        found = find_amounts([text])
        got = found[0] if found else None
        if got != expected:
            failed += 1
            print(f"[selftest] FAIL {text!r}: {got} != {expected}")
    print(f"[selftest] parser: {len(cases) - failed}/{len(cases)} lulus")

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[selftest] Pillow tidak ada, lewati tes OCR")
        return 0 if failed == 0 else 1

    img = Image.new("RGB", (900, 600), (12, 14, 18))
    draw = ImageDraw.Draw(img)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font_big = ImageFont.truetype(str(font_path), 46) if font_path.exists() else ImageFont.load_default()
    font_mid = ImageFont.truetype(str(font_path), 64) if font_path.exists() else ImageFont.load_default()
    draw.text((60, 50), "Pembayaran Berhasil", fill=(235, 235, 235), font=font_big)
    draw.text((60, 160), "Total Bayar", fill=(160, 160, 160), font=font_big)
    draw.text((60, 240), "Rp 250.000", fill=(120, 255, 160), font=font_mid)
    draw.text((60, 360), "QRIS - Toko Contoh", fill=(190, 190, 190), font=font_big)
    test_path = ROOT / "build" / "ocr_selftest.png"
    ensure_dirs(test_path.parent)
    img.save(test_path)

    engine = PaddleEngine(lang="en")
    frame = cv2.imread(str(test_path))
    if frame is None:
        print("[selftest] gagal membuat gambar uji")
        return 1
    items = engine.read(frame)
    texts = [t for t, _, _ in items]
    amounts = find_amounts(texts)
    print("[selftest] teks OCR:", " | ".join(texts))
    print("[selftest] nominal:", [fmt_rupiah(a) for a in amounts])
    ok = 250000 in amounts
    print(f"[selftest] OCR gambar sintetis: {'OK' if ok else 'GAGAL'}")
    return 0 if ok and failed == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="OCR nominal pembayaran dari layar HP")
    ap.add_argument("--source", default="0", help="index webcam / path video")
    ap.add_argument("--image", default="", help="mode gambar tunggal")
    ap.add_argument(
        "--roi",
        default=None,
        help="x,y,w,h (fraksi atau piksel). default: tengah frame (webcam), penuh (--image)",
    )
    ap.add_argument("--lang", default="en", help="kode bahasa PaddleOCR (en/latin tergantung versi)")
    ap.add_argument("--mode", default="clahe", choices=["raw", "clahe", "binary"])
    ap.add_argument("--every", type=int, default=4, help="OCR tiap N frame (minimum)")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--votes", type=int, default=3, help="jumlah kesepakatan untuk verifikasi")
    ap.add_argument("--cooldown", type=float, default=4.0, help="jeda snapshot nominal sama (detik)")
    ap.add_argument("--out", default=str(ROOT / "ocr_captures"))
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    engine = PaddleEngine(lang=args.lang)

    if args.image:
        return run_image(engine, args)

    out_dir = Path(args.out)
    ensure_dirs(out_dir)
    log_path = out_dir / "payments.csv"
    new_log = not log_path.exists()
    log_fh = log_path.open("a", newline="", encoding="utf-8")
    writer = csv.writer(log_fh)
    if new_log:
        writer.writerow(["timestamp", "amount", "snapshot"])

    source = int(args.source) if str(args.source).isdigit() else args.source
    cap = open_capture(source)
    if isinstance(source, int):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    votes: deque[int] = deque(maxlen=12)
    confirmed: int | None = None
    confirmed_at = 0.0
    last_snapshot: dict[int, float] = {}
    frames = 0
    next_ocr = 0.0
    ocr_ms = 0.0
    mode = args.mode
    debug = False
    roi = None
    items: list[tuple[str, float, object | None]] = []
    scale = 1.0
    live: int | None = None
    texts: list[str] = []

    print(f"[ocr] output: {out_dir} | q keluar, s snapshot, b preprocess, d debug, r reset")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames += 1
            h, w = frame.shape[:2]
            if roi is None:
                roi = parse_roi(args.roi or "0.25,0.25,0.5,0.5", w, h)
            x, y, rw, rh = roi

            now = time.time()
            if now >= next_ocr:
                t0 = time.perf_counter()
                crop, scale = preprocess(frame[y:y + rh, x:x + rw], mode)
                items = engine.read(crop)
                texts = [t for t, _, _ in items]
                amounts = find_amounts(texts)

                if not amounts and mode != "raw":
                    crop_raw, scale_raw = preprocess(frame[y:y + rh, x:x + rw], "raw")
                    items_raw = engine.read(crop_raw)
                    amounts = find_amounts([t for t, _, _ in items_raw])
                    if amounts:
                        items, scale = items_raw, scale_raw

                live = amounts[0] if amounts else None
                if live is not None:
                    votes.append(live)
                ocr_ms = (time.perf_counter() - t0) * 1000
                next_ocr = time.time() + max(args.every / 30.0, ocr_ms / 1000.0 * 1.1)

                if votes:
                    amount, count = Counter(votes).most_common(1)[0]
                    if count >= args.votes:
                        if confirmed != amount:
                            print(f"[ocr] TERVERIFIKASI {fmt_rupiah(amount)} ({count}/{len(votes)} vote)")
                            confirmed = amount
                            confirmed_at = time.time()
                        if time.time() - last_snapshot.get(amount, 0.0) > args.cooldown:
                            path = save_snapshot(frame, amount, out_dir, writer, log_fh)
                            last_snapshot[amount] = time.time()
                            print(f"[ocr] snapshot: {path.name}")

            if confirmed is not None and time.time() - confirmed_at > 6:
                confirmed = None
                votes.clear()

            status = f"OCR {ocr_ms:.0f} ms | {mode} | vote {dict(Counter(votes))}" if debug else f"OCR {ocr_ms:.0f} ms | {mode}"
            view = draw_panel(frame, roi, live, confirmed, status, debug)
            if debug:
                draw_ocr_boxes(view, roi, items, scale)

            if not args.no_window:
                cv2.imshow("OCR pembayaran - layar HP", view)
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    break
                if key == ord("b"):
                    mode = {"clahe": "binary", "binary": "raw", "raw": "clahe"}[mode]
                    print(f"[ocr] preprocess -> {mode}")
                if key == ord("d"):
                    debug = not debug
                if key == ord("r"):
                    votes.clear()
                    confirmed = None
                    print("[ocr] reset vote")
                if key == ord("s"):
                    target = confirmed or live
                    if target:
                        path = save_snapshot(view, target, out_dir, writer, log_fh)
                        print(f"[ocr] snapshot manual: {path.name}")
                    else:
                        print("[ocr] belum ada nominal untuk disimpan")
            if args.max_frames and frames >= args.max_frames:
                break
    finally:
        cap.release()
        log_fh.close()
        cv2.destroyAllWindows()

    print(f"[ocr] selesai: {frames} frame, log {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
