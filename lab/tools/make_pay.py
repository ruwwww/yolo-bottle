from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "pay_500k.png"

img = np.full((900, 700, 3), 255, np.uint8)
lines = [
    ("BUKTI PEMBAYARAN", 0.9, 70, 2),
    ("TOTAL : Rp 500.000", 1.4, 200, 3),
    ("REF : DEMO123456", 0.9, 320, 2),
    ("STATUS : BERHASIL", 0.9, 420, 2),
]
for text, scale, y, thick in lines:
    cv2.putText(img, text, (40, y), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (0, 0, 0), thick, cv2.LINE_AA)
cv2.imwrite(str(OUT), img)
print(f"ok: {OUT}")
