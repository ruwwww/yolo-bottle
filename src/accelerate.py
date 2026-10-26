"""Loader model tercepat untuk hardware ini (Intel Iris Xe + CPU).

OpenVINO di GPU iGPU: ~20 ms/frame (imgsz 640) vs torch CPU ~148 ms.
Ekspor otomatis sekali saja ke `<nama>_openvino_model/`.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

OPENVINO_DEVICES = ("auto", "openvino", "intel:gpu", "intel:cpu")


def openvino_available() -> bool:
    try:
        import openvino  # noqa: F401
    except ImportError:
        return False
    return True


def _enable_ov_cache() -> None:
    """Cache kernel OpenVINO supaya start kedua tidak kompilasi ulang (GPU ~10-15 s)."""
    try:
        import openvino as ov
    except ImportError:
        return
    if getattr(ov.Core, "_yolo_cache_patch", False):
        return
    cache = Path(__file__).resolve().parent.parent / "build" / "ov_cache"
    cache.mkdir(parents=True, exist_ok=True)
    base = ov.Core

    class CachedCore(base):  # type: ignore[misc, valid-type]
        _yolo_cache_patch = True

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            try:
                self.set_property({"CACHE_DIR": str(cache)})
            except Exception:  # noqa: BLE001
                pass

    ov.Core = CachedCore


def load_detector(path: str | Path, imgsz: int = 640, device: str = "auto"):
    """Kembalikan (model, device_arg). device_arg dipakai di model.predict()."""
    from ultralytics import YOLO

    p = Path(path)
    device = device.lower()
    if device not in OPENVINO_DEVICES:
        return YOLO(str(p)), ("0" if device == "cuda" else device)

    if not openvino_available():
        print("[fast] openvino belum terpasang -> pakai torch CPU")
        return YOLO(str(p)), "cpu"

    if p.suffix.lower() == ".pt":
        ov_dir = p.parent / f"{p.stem}_openvino_model"
        needs_export = not ov_dir.exists()
        if not needs_export:
            # Re-export jika file .pt lebih baru daripada folder OpenVINO
            try:
                if p.stat().st_mtime > ov_dir.stat().st_mtime:
                    needs_export = True
            except OSError:
                pass

        if needs_export:
            print(f"[fast] ekspor OpenVINO sekali saja: {ov_dir.name} (imgsz {imgsz})")
            YOLO(str(p)).export(format="openvino", imgsz=imgsz, half=False, dynamic=False)
        p = ov_dir

    _enable_ov_cache()
    model = YOLO(str(p))
    target = "intel:CPU" if device == "intel:cpu" else "intel:GPU"
    try:
        model.predict(
            np.zeros((imgsz, imgsz, 3), dtype=np.uint8),
            imgsz=imgsz,
            device=target,
            verbose=False,
        )
        print(f"[fast] OpenVINO {target} siap (imgsz {imgsz})")
        return model, target
    except Exception as exc:  # noqa: BLE001
        print(f"[fast] {target} gagal ({exc}); fallback OpenVINO CPU")
        return model, "intel:CPU"


def tune_threads(threads: int = 6) -> None:
    """Batasi thread agar kamera + OCR tidak berebut CPU."""
    try:
        import torch

        torch.set_num_threads(max(1, threads))
    except Exception:  # noqa: BLE001
        pass
    import cv2

    cv2.setNumThreads(1)
