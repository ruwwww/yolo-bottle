from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "pay_plain.png"

img = np.full((700, 700, 3), 255, np.uint8)
cv2.putText(img, "TOTAL BAYAR", (40, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
            (0, 0, 0), 3, cv2.LINE_AA)
cv2.putText(img, "Rp 500.000", (40, 320), cv2.FONT_HERSHEY_SIMPLEX, 1.6,
            (0, 0, 0), 4, cv2.LINE_AA)
cv2.imwrite(str(OUT), img)
print(f"ok: {OUT}")
