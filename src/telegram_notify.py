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

import argparse
import html
import json
import mimetypes
import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib import parse, request

# Load environment variable jika ada modul common
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_env

    load_env()
except Exception:
    pass

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


def update_env(key: str, value: str, env_path: Path | None = None) -> Path:
    """Tulis / perbarui nilai konfigurasi ke file .env."""
    p = env_path or (Path(__file__).resolve().parents[1] / ".env")
    lines: list[str] = []
    found = False
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            clean = line.strip()
            if clean.startswith(f"{key}=") or clean == key:
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    p.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    os.environ[key] = value
    return p


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

    def get_me(self) -> dict | None:
        """Cek validitas bot token dan ambil profil bot."""
        if not self.token:
            self.last_error = "token belum diisi"
            return None
        try:
            req = request.Request(f"{self._api}/getMe")
            with request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("ok"):
                return payload.get("result")
            self.last_error = str(payload.get("description") or "gagal getMe")
            return None
        except Exception as exc:
            self.last_error = str(exc)
            return None

    def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        """Ambil update pesan masuk ke bot."""
        if not self.token:
            self.last_error = "token belum diisi"
            return []
        try:
            params: dict[str, str] = {}
            if offset is not None:
                params["offset"] = str(offset)
            if timeout > 0:
                params["timeout"] = str(timeout)
            qs = f"?{parse.urlencode(params)}" if params else ""
            req = request.Request(f"{self._api}/getUpdates{qs}")
            with request.urlopen(req, timeout=max(self.timeout, timeout + 5)) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("ok"):
                return payload.get("result", [])
            self.last_error = str(payload.get("description") or "gagal getUpdates")
            return []
        except Exception as exc:
            self.last_error = str(exc)
            return []

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
            "⚠️ <b>PELANGGARAN KASIR KULKAS</b>",
            f"📅 Waktu: {(waktu or datetime.now()).strftime('%Y-%m-%d %H:%M:%S')}",
            f"🥤 Minuman: {html.escape(str(minuman))}",
            f"💰 Total: Rp {int(total):,}".replace(",", "."),
            f"💵 Dibayar: Rp {int(dibayar):,}".replace(",", "."),
            f"❌ Kurang: Rp {int(kurang):,}".replace(",", "."),
            f"🔖 Ref: {html.escape(str(referensi))}",
        ]
        if catatan:
            lines.append(f"📝 Catatan: {html.escape(catatan)}")
        return self.send_photo(photo, "\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="Utilitas Bot Telegram Kasir Kulkas")
    parser.add_argument("--info", action="store_true", help="Cek status bot & chat ID saat ini")
    parser.add_argument("--test", nargs="?", const="Kasir Kulkas: tes koneksi bot Telegram berhasil! 🚀",
                        help="Kirim pesan uji ke chat ID yang dikonfigurasi")
    parser.add_argument("--test-photo", help="Kirim foto uji ke chat ID")
    parser.add_argument("--listen", action="store_true",
                        help="Tunggu pesan baru (/start) untuk otomatis mendeteksi dan menyimpan CHAT_ID ke .env")
    parser.add_argument("--set-chat", help="Simpan chat ID baru ke file .env")
    args = parser.parse_args()

    bot = TelegramBot()

    if args.set_chat:
        update_env(CHAT_ENV, args.set_chat.strip())
        print(f"[telegram] TELEGRAM_CHAT_ID berhasil diupdate ke: {args.set_chat.strip()}")
        return 0

    if args.listen:
        print("[telegram] Memeriksa koneksi bot...")
        me = bot.get_me()
        if not me:
            print(f"[telegram] Gagal menghubungi bot: {bot.last_error}")
            return 1
        username = me.get("username", "bot")
        print(f"[telegram] Terhubung ke bot @{username} ('{me.get('first_name')}')")
        print(f"[telegram] Silakan buka Telegram, cari @{username} (https://t.me/{username}),")
        print("[telegram] lalu tekan tombol START atau kirim pesan apa saja...")

        found_chat = None
        user_name = ""
        # Cek update terbaru
        updates = bot.get_updates()
        if updates:
            last = updates[-1]
            msg = last.get("message") or last.get("edited_message") or {}
            chat = msg.get("chat", {})
            if chat.get("id"):
                found_chat = str(chat["id"])
                user_name = chat.get("first_name") or chat.get("username") or str(found_chat)

        if not found_chat:
            start_t = time.time()
            while time.time() - start_t < 60:
                time.sleep(2)
                sys.stdout.write(".")
                sys.stdout.flush()
                updates = bot.get_updates()
                if updates:
                    last = updates[-1]
                    msg = last.get("message") or last.get("edited_message") or {}
                    chat = msg.get("chat", {})
                    if chat.get("id"):
                        found_chat = str(chat["id"])
                        user_name = chat.get("first_name") or chat.get("username") or str(found_chat)
                        print("")
                        break
            if not found_chat:
                print("\n[telegram] Batas waktu 60 detik habis. Belum ada pesan masuk.")
                return 1

        print(f"[telegram] Pesan terdeteksi dari: {user_name} (Chat ID: {found_chat})")
        update_env(CHAT_ENV, found_chat)
        print(f"[telegram] Disimpan ke .env: TELEGRAM_CHAT_ID={found_chat}")
        bot.chat_id = found_chat
        ok = bot.send_message(
            f"✅ <b>Koneksi Berhasil!</b>\n"
            f"Halo {html.escape(user_name)}, bot kasir kulkas sekarang terhubung ke chat ini.\n"
            f"Notifikasi pelanggaran kasir akan dikirimkan ke sini."
        )
        if ok:
            print("[telegram] Pesan konfirmasi telah dikirim ke Telegram!")
        else:
            print(f"[telegram] Gagal mengirim konfirmasi: {bot.last_error}")
        return 0

    if args.test_photo:
        if not bot.enabled:
            print("[telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diisi lengkap.")
            return 1
        print(f"[telegram] Mengirim foto '{args.test_photo}' ke chat {bot.chat_id}...")
        ok = bot.send_photo(args.test_photo, caption="📸 Kasir Kulkas: tes pengiriman foto")
        print("[telegram] Foto tes:", "BERHASIL" if ok else f"GAGAL - {bot.last_error}")
        return 0 if ok else 1

    if args.test is not None:
        if not bot.enabled:
            print("[telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diisi lengkap.")
            return 1
        print(f"[telegram] Mengirim pesan tes ke chat {bot.chat_id}...")
        ok = bot.send_message(args.test)
        print("[telegram] Pesan tes:", "BERHASIL" if ok else f"GAGAL - {bot.last_error}")
        return 0 if ok else 1

    # Default / --info
    print("=== Status Integrasi Telegram Bot ===")
    me = bot.get_me()
    if me:
        print(f"Bot Status  : ONLINE")
        print(f"Bot Name    : {me.get('first_name')}")
        print(f"Bot Username: @{me.get('username')}")
        print(f"Bot ID      : {me.get('id')}")
    else:
        print(f"Bot Status  : ERROR ({bot.last_error})")
    print(f"Token Config: {'TERPASANG' if bot.token else 'KOSONG'}")
    print(f"Chat ID     : {bot.chat_id or 'BELUM DIISI'}")
    print(f"Notifikasi  : {'AKTIF' if bot.enabled else 'TIDAK AKTIF (perlu token + chat_id)'}")
    print("=====================================")
    print("Perintah yang tersedia:")
    print("  python src/telegram_notify.py --test            (kirim pesan uji)")
    print("  python src/telegram_notify.py --listen          (deteksi chat id otomatis)")
    print("  python src/telegram_notify.py --set-chat <id>   (simpan chat id manual)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
