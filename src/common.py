from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DATASET = ROOT / "dataset"
RAW_IMAGES = DATASET / "raw" / "images"
RAW_LABELS = DATASET / "raw" / "labels"
WEIGHTS = ROOT / "weights"
RUNS = ROOT / "runs"
BUILD = ROOT / "build"

CLASSES = ["bottle"]
BOTTLE_COCO_ID = 39


def load_env(path: Path | None = None) -> None:
    """Minimal .env loader (KEY=VALUE, # comments) without extra dependencies."""
    p = path or ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


def ensure_dirs(*dirs: Path | str) -> None:
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)


def add_root_to_path() -> None:
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def fmt_rupiah(amount: int | float) -> str:
    return "Rp " + f"{int(round(amount)):,}".replace(",", ".")


def screen_size() -> tuple[int, int]:
    """Resolusi layar utama (Windows DPI-aware), fallback 1920x1080."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:  # noqa: BLE001 - non-Windows / headless
        return 1920, 1080


def letterbox(img, size: tuple[int, int]):
    """Skala gambar ke dalam kanvas `size` tanpa merusak aspek (letterbox)."""
    import cv2
    import numpy as np

    w, h = size
    ih, iw = img.shape[:2]
    skala = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * skala)), max(1, int(ih * skala))
    kanvas = np.zeros((h, w, 3), dtype=img.dtype)
    x, y = (w - nw) // 2, (h - nh) // 2
    kanvas[y : y + nh, x : x + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    return kanvas


def open_capture(source):
    """Open webcam (int) or video path with the cheapest backend for Windows."""
    import cv2

    if isinstance(source, str) and not source.isdigit():
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise RuntimeError(f"Tidak bisa membuka sumber: {source}")
        return cap

    index = int(source)
    if sys.platform.startswith("win"):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(index)
    else:
        cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise RuntimeError(
            f"Webcam index {index} tidak bisa dibuka. Cek kamera / coba --source 1"
        )
    return cap
