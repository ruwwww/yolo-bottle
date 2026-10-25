"""Notifikasi pelanggaran kasir kulkas ke Telegram bot (stdlib, tanpa dependency).

Konfigurasi lewat environment (dibaca dari `.env`):
  TELEGRAM_BOT_TOKEN=123456:ABC...
  TELEGRAM_CHAT_ID=123456789

Contoh:
  from telegram_notify import TelegramBot
  bot = TelegramBot()
  if bot.enabled:
      bot.notify_violation("pelanggaran/foto.jpg", total=50000, dibayar=3000,
                           minuman="1x Air Mineral")
"""
from __future__ import annotations

import html
import json
import mimetypes
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from urllib import parse, request

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHAT_ENV = "TELEGRAM_CHAT_ID"


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]):
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(f"{value}\r\n".encode())
    for name, (filename, data, ctype) in files.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        )
        parts.append(f"Content-Type: {ctype}\r\n\r\n".encode())
        parts.append(data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(parts)


class TelegramBot:
    def __init__(self, token: str = "", chat_id: str = "", timeout: float = 25.0):
        self.token = (token or os.environ.get(TOKEN_ENV, "")).strip()
        self.chat_id = (chat_id or os.environ.get(CHAT_ENV, "")).strip()
        self.timeout = timeout
        self.last_error = ""
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    @property
    def _api(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def _call(
        self,
        method: str,
        fields: dict[str, str],
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> bool:
        if not self.enabled:
            self.last_error = "token/chat_id belum diisi"
            return False
        try:
            with self._lock:
                if files:
                    boundary, body = _multipart(fields, files)
                    req = request.Request(
                        f"{self._api}/{method}",
                        data=body,
                        method="POST",
                        headers={
                            "Content-Type": f"multipart/form-data; boundary={boundary}"
                        },
                    )
                else:
                    req = request.Request(
                        f"{self._api}/{method}",
                        data=parse.urlencode(fields).encode(),
                        method="POST",
                    )
                with request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            if not payload.get("ok"):
                self.last_error = str(payload.get("description") or "tidak diketahui")
                return False
            self.last_error = ""
            return True
        except Exception as exc:  # noqa: BLE001 - jangan sampai mematikan kasir
            self.last_error = str(exc)
            return False

    def send_message(self, text: str) -> bool:
        return self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )

    def send_photo(self, photo: str | Path, caption: str = "") -> bool:
        path = Path(photo)
        if not path.exists():
            self.last_error = f"foto tidak ada: {path}"
            return False
        ctype = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        fields = {"chat_id": self.chat_id, "parse_mode": "HTML"}
        if caption:
            fields["caption"] = caption
        files = {"photo": (path.name, path.read_bytes(), ctype)}
        return self._call("sendPhoto", fields, files)

    def notify_violation(
        self,
        photo: str | Path,
        *,
        total: int,
        dibayar: int,
        minuman: str = "-",
        referensi: str = "-",
        kurang: int | None = None,
        waktu: datetime | None = None,
        catatan: str = "",
    ) -> bool:
        if not self.enabled:
            return False
        if kurang is None:
            kurang = max(0, int(total) - int(dibayar))
        lines = [
            "<b>PELANGGARAN KASIR KULKAS</b>",
            f"Waktu: {(waktu or datetime.now()).strftime('%Y-%m-%d %H:%M:%S')}",
            f"Minuman: {html.escape(str(minuman))}",
            f"Total: Rp {int(total):,}".replace(",", "."),
            f"Dibayar: Rp {int(dibayar):,}".replace(",", "."),
            f"Kurang: Rp {int(kurang):,}".replace(",", "."),
            f"Ref: {html.escape(str(referensi))}",
        ]
        if catatan:
            lines.append(f"Catatan: {html.escape(catatan)}")
        return self.send_photo(photo, "\n".join(lines))
