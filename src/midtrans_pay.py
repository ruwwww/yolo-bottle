"""Integrasi Midtrans Payment Gateway untuk Kasir Kulkas Kejujuran (stdlib, tanpa dependency).

Fitur:
  - Snap API: buat token pembayaran & URL redirect
  - Core API: polling status transaksi (/v2/{order_id}/status)
  - QR Code generator (menggunakan OpenCV built-in QRCodeEncoder)
  - Pembayaran dinamis via QRIS / GoPay / ShopeePay / Bank Virtual Account

Konfigurasi (.env):
  MIDTRANS_SERVER_KEY=Mid-server-...
  MIDTRANS_CLIENT_KEY=Mid-client-...
  MIDTRANS_MERCHANT_ID=M...
  MIDTRANS_IS_PRODUCTION=false
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib import error, request

# Load environment variable jika ada modul common
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_env

    load_env()
except Exception:
    pass

ENV_SERVER_KEY = "MIDTRANS_SERVER_KEY"
ENV_CLIENT_KEY = "MIDTRANS_CLIENT_KEY"
ENV_MERCHANT_ID = "MIDTRANS_MERCHANT_ID"
ENV_PRODUCTION = "MIDTRANS_IS_PRODUCTION"


class MidtransGateway:
    def __init__(
        self,
        server_key: str = "",
        client_key: str = "",
        merchant_id: str = "",
        is_production: bool | None = None,
        timeout: float = 15.0,
    ):
        self.server_key = (server_key or os.environ.get(ENV_SERVER_KEY, "")).strip()
        self.client_key = (client_key or os.environ.get(ENV_CLIENT_KEY, "")).strip()
        self.merchant_id = (merchant_id or os.environ.get(ENV_MERCHANT_ID, "")).strip()
        if is_production is None:
            is_production = os.environ.get(ENV_PRODUCTION, "false").lower() in ("1", "true", "yes")
        self.is_production = is_production
        self.timeout = timeout
        self.last_error = ""
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.server_key)

    @property
    def snap_base_url(self) -> str:
        if self.is_production:
            return "https://app.midtrans.com/snap/v1"
        return "https://app.sandbox.midtrans.com/snap/v1"

    @property
    def api_base_url(self) -> str:
        if self.is_production:
            return "https://api.midtrans.com"
        return "https://api.sandbox.midtrans.com"

    def _auth_header(self) -> dict[str, str]:
        auth_bytes = f"{self.server_key}:".encode("utf-8")
        auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")
        return {
            "Authorization": f"Basic {auth_b64}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def create_snap_transaction(
        self,
        order_id: str,
        gross_amount: int,
        item_details: list[dict] | None = None,
        customer_details: dict | None = None,
    ) -> dict | None:
        """Buat transaksi Snap baru di Midtrans."""
        if not self.enabled:
            self.last_error = "MIDTRANS_SERVER_KEY belum dikonfigurasi"
            return None

        payload: dict = {
            "transaction_details": {
                "order_id": order_id,
                "gross_amount": int(gross_amount),
            }
        }
        if item_details:
            payload["item_details"] = item_details
        if customer_details:
            payload["customer_details"] = customer_details
        else:
            payload["customer_details"] = {
                "first_name": "Pelanggan Kasir Kulkas",
            }

        url = f"{self.snap_base_url}/transactions"
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=body, headers=self._auth_header(), method="POST")

        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.last_error = ""
            return data
        except error.HTTPError as err:
            raw = err.read().decode("utf-8", errors="replace")
            self.last_error = f"HTTP {err.code}: {raw}"
            return None
        except Exception as exc:
            self.last_error = str(exc)
            return None

    def check_status(self, order_id: str) -> dict | None:
        """Cek status transaksi Midtrans via Core API (/v2/{order_id}/status)."""
        if not self.enabled:
            self.last_error = "MIDTRANS_SERVER_KEY belum dikonfigurasi"
            return None

        url = f"{self.api_base_url}/v2/{order_id}/status"
        headers = self._auth_header()
        headers.pop("Content-Type", None)
        req = request.Request(url, headers=headers, method="GET")

        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.last_error = ""
            return data
        except error.HTTPError as err:
            # 404 biasa terjadi sebelum pelanggan membuka halaman bayar / memilih metode
            raw = err.read().decode("utf-8", errors="replace")
            self.last_error = f"HTTP {err.code}: {raw}"
            return None
        except Exception as exc:
            self.last_error = str(exc)
            return None

    def cancel_transaction(self, order_id: str) -> bool:
        """Batalkan transaksi (/v2/{order_id}/cancel)."""
        if not self.enabled:
            return False
        url = f"{self.api_base_url}/v2/{order_id}/cancel"
        req = request.Request(url, data=b"{}", headers=self._auth_header(), method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("status_code") in ("200", "201")
        except Exception as exc:
            self.last_error = str(exc)
            return False

    @staticmethod
    def is_settled(status_data: dict | None) -> bool:
        """Periksa apakah pembayaran sudah lunas / sukses."""
        if not status_data:
            return False
        tx_status = str(status_data.get("transaction_status", "")).lower()
        fraud_status = str(status_data.get("fraud_status", "")).lower()
        if tx_status == "settlement":
            return True
        if tx_status == "capture" and fraud_status == "accept":
            return True
        return False

    @staticmethod
    def generate_qr_image(text: str, size: int = 180, border: int = 4):
        """Buat gambar matriks QR code BGR menggunakan OpenCV built-in QRCodeEncoder."""
        try:
            import cv2
            import numpy as np

            enc = cv2.QRCodeEncoder.create()
            qr = enc.encode(text)
            if qr is None:
                return None
            # qr: 2D uint8, nilai 0 (hitam) dan 255 (putih)
            qr_bordered = np.pad(qr, pad_width=border, mode="constant", constant_values=255)
            resized = cv2.resize(
                qr_bordered, (size, size), interpolation=cv2.INTER_NEAREST
            )
            # Ubah ke 3-channel BGR
            bgr = cv2.cvtColor(resized, cv2.COLOR_GRAY2BGR)
            return bgr
        except Exception:
            return None

    @staticmethod
    def print_ascii_qr(text: str) -> None:
        """Cetak representasi QR code sederhana di terminal console."""
        try:
            if hasattr(sys.stdout, "reconfigure"):
                try:
                    sys.stdout.reconfigure(encoding="utf-8")
                except Exception:
                    pass

            import cv2
            import numpy as np

            enc = cv2.QRCodeEncoder.create()
            qr = enc.encode(text)
            if qr is None:
                print("Gagal generate QR")
                return
            qr_pad = np.pad(qr, 2, constant_values=255)
            # Cetak terminal dengan double-blocks
            h, w = qr_pad.shape
            for r in range(0, h, 2):
                line = ""
                for c in range(w):
                    top = qr_pad[r, c] == 0
                    bot = (qr_pad[r + 1, c] == 0) if r + 1 < h else False
                    if top and bot:
                        line += "█"
                    elif top and not bot:
                        line += "▀"
                    elif not top and bot:
                        line += "▄"
                    else:
                        line += " "
                try:
                    print("  " + line)
                except UnicodeEncodeError:
                    # Fallback karakter ascii standar
                    fallback = "".join("##" if qr_pad[r, c] == 0 else "  " for c in range(w))
                    print("  " + fallback)
        except Exception as exc:
            print("Cetak ASCII QR tidak tersedia:", exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Midtrans Payment Gateway Test & Utility")
    parser.add_argument("--info", action="store_true", help="Cek kredensial dan status Midtrans")
    parser.add_argument("--create", type=int, help="Buat transaksi Snap uji dengan nominal (misal: 10000)")
    parser.add_argument("--items", help="Keterangan nama item (misal: '1x Teh Pucuk, 2x Golda')")
    parser.add_argument("--status", help="Cek status transaksi berdasarkan order_id")
    parser.add_argument("--poll", help="Monitor order_id sampai status pembayaran selesai")
    parser.add_argument("--cancel", help="Batalkan transaksi berdasarkan order_id")
    parser.add_argument("--test", action="store_true", help="Jalankan tes pembuatan transaksi Snap")
    args = parser.parse_args()

    gw = MidtransGateway()

    if args.status:
        print(f"[midtrans] Memeriksa status order: {args.status} ...")
        res = gw.check_status(args.status)
        if res:
            print(json.dumps(res, indent=2))
            status = res.get("transaction_status")
            print(f"[midtrans] Status: {status.upper()} (Lunas: {gw.is_settled(res)})")
            return 0
        print(f"[midtrans] Gagal / belum ada: {gw.last_error}")
        return 1

    if args.cancel:
        print(f"[midtrans] Membatalkan order: {args.cancel} ...")
        ok = gw.cancel_transaction(args.cancel)
        print("[midtrans] Hasil pembatalan:", "BERHASIL" if ok else f"GAGAL ({gw.last_error})")
        return 0 if ok else 1

    if args.poll:
        order_id = args.poll
        print(f"[midtrans] Menunggu pembayaran untuk order: {order_id} (maks 90s)...")
        start = time.time()
        while time.time() - start < 90:
            res = gw.check_status(order_id)
            if res:
                st = res.get("transaction_status", "")
                sys.stdout.write(f"\r[midtrans] Status saat ini: {st} ...   ")
                sys.stdout.flush()
                if gw.is_settled(res):
                    print(f"\n[midtrans] PEMBAYARAN BERHASIL! (Nominal: {res.get('gross_amount')})")
                    return 0
            else:
                sys.stdout.write(".")
                sys.stdout.flush()
            time.sleep(2)
        print("\n[midtrans] Waktu tunggu habis.")
        return 1

    if args.create or args.test:
        amount = args.create or 12000
        order_id = f"KASIR-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
        item_name = args.items or "Minuman Kasir Kulkas"
        items = [{"id": "ITEM-1", "price": amount, "quantity": 1, "name": item_name[:50]}]

        print(f"[midtrans] Membuat transaksi: {order_id} (Rp {amount:,}) ...")
        res = gw.create_snap_transaction(order_id, amount, items)
        if not res:
            print(f"[midtrans] GAGAL: {gw.last_error}")
            return 1

        redirect_url = res.get("redirect_url", "")
        token = res.get("token", "")
        print(f"[midtrans] Transaksi Snap Berhasil Dibuat!")
        print(f"  Order ID    : {order_id}")
        print(f"  Snap Token  : {token}")
        print(f"  Payment URL : {redirect_url}")
        print("\nScan QR Code di bawah dengan HP untuk membayar:")
        gw.print_ascii_qr(redirect_url)
        print("\nBuka URL di atas di browser untuk simulasi pembayaran.")
        print(f"Cek status dengan: python src/midtrans_pay.py --status {order_id}")
        print(f"Polling status  : python src/midtrans_pay.py --poll {order_id}")
        return 0

    # Default: --info
    print("=== Status Integrasi Midtrans ===")
    print(f"Merchant ID : {gw.merchant_id or 'BELUM DIISI'}")
    print(f"Client Key  : {gw.client_key or 'BELUM DIISI'}")
    print(f"Server Key  : {'TERPASANG' if gw.server_key else 'BELUM DIISI'}")
    print(f"Mode        : {'PRODUCTION' if gw.is_production else 'SANDBOX'}")
    print(f"API Base URL: {gw.api_base_url}")
    print(f"Status      : {'SIAP' if gw.enabled else 'TIDAK AKTIF (perlu Server Key)'}")
    print("=================================")
    print("Perintah yang tersedia:")
    print("  python src/midtrans_pay.py --test              (buat transaksi tes)")
    print("  python src/midtrans_pay.py --create 15000      (buat transaksi nominal tertentu)")
    print("  python src/midtrans_pay.py --status <order_id> (cek status transaksi)")
    print("  python src/midtrans_pay.py --poll <order_id>   (tunggu pembayaran lunas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
