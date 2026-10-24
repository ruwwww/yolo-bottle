"""Klien HTTP untuk server Moondream Photon (deteksi bbox dari prompt teks)."""
from __future__ import annotations

import base64
import http.client
import json
import urllib.error
import urllib.parse

import cv2

DEFAULT_URL = "http://100.91.215.116:8090"


class MoondreamEngine:
    """Panggil /detect di server GPU; koordinat dinormalisasi -> piksel frame."""

    def __init__(self, url: str, prompt: str = "bottle", label: str = "",
                 resize: int = 640, timeout: float = 10.0):
        self.url = url.rstrip("/")
        self.prompt = prompt
        self.label = label
        self.resize = resize
        self.timeout = timeout
        self.last_ms = 0.0
        self.last_error = ""
        u = urllib.parse.urlparse(self.url)
        self._host = u.hostname or "127.0.0.1"
        self._port = u.port or 80
        self._path = u.path or ""
        self._conn: http.client.HTTPConnection | None = None

    def _koneksi(self) -> http.client.HTTPConnection:
        if self._conn is None:
            self._conn = http.client.HTTPConnection(self._host, self._port, timeout=self.timeout)
        return self._conn

    def _tutup(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None

    def _req(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        last: Exception | None = None
        for _ in range(2):
            try:
                conn = self._koneksi()
                conn.request(method, self._path + path, body=body, headers=headers)
                r = conn.getresponse()
                data = r.read()
                if r.status >= 400:
                    raise RuntimeError(f"HTTP {r.status}: {data[:200]!r}")
                return json.loads(data.decode())
            except (http.client.HTTPException, OSError, ValueError, RuntimeError) as exc:
                last = exc
                self._tutup()
        raise RuntimeError(str(last))

    def health(self) -> bool:
        try:
            return bool(self._req("GET", "/health").get("ok"))
        except RuntimeError as exc:
            self.last_error = str(exc)
            return False

    def detect(self, frame) -> list[tuple[int, int, int, int, float, int]]:
        h, w = frame.shape[:2]
        kirim = frame
        if self.resize and max(h, w) > self.resize:
            s = self.resize / max(h, w)
            kirim = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", kirim, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return []
        payload = {
            "image": base64.b64encode(buf.tobytes()).decode(),
            "prompt": self.prompt,
            "label": self.label,
        }
        try:
            res = self._req("POST", "/detect", payload)
        except RuntimeError as exc:
            self.last_error = str(exc)
            print(f"[moondream] error: {exc}")
            return []
        self.last_ms = float(res.get("ms", 0.0))
        out = []
        for o in res.get("objects", []):
            x1 = int(o["x_min"] * w)
            y1 = int(o["y_min"] * h)
            x2 = int(o["x_max"] * w)
            y2 = int(o["y_max"] * h)
            out.append((x1, y1, x2, y2, 1.0, 0))
        return out
