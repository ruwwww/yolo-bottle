"""Server HTTP Webhook Lokal untuk Menerima Mutasi Midtrans / Bank (stdlib, tanpa dependency).

Endpoints:
  - GET  /health          : Status server webhook
  - GET  /mutasi          : Daftar mutasi yang masuk
  - POST /webhook/midtrans: Menerima notifikasi transaksi dari Midtrans (QRIS Statis / Snap)
  - POST /webhook/manual  : Inject mutasi manual untuk pengujian
"""
from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

# Load modul reconcile & common
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import ROOT, load_env
    from reconcile import MutationStore, verify_midtrans_signature

    load_env()
except Exception:
    ROOT = Path(__file__).resolve().parents[1]
    from reconcile import MutationStore, verify_midtrans_signature

ENV_SERVER_KEY = "MIDTRANS_SERVER_KEY"
DASHBOARD_HTML_PATH = Path(__file__).resolve().parent / "dashboard.html"
TRANSAKSI_CSV_PATH = ROOT / "transaksi.csv"
PELANGGARAN_DIR = ROOT / "pelanggaran"


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class WebhookHandler(BaseHTTPRequestHandler):
    store: MutationStore = MutationStore()
    server_key: str = ""

    def log_message(self, format, *args):
        # Override agar logging ringkas
        sys.stdout.write(f"[webhook] {self.address_string()} - {format % args}\n")
        sys.stdout.flush()

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, html_str: str):
        body = html_str.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: Path, ctype: str = "image/jpeg"):
        if not file_path.exists() or not file_path.is_file():
            self._send_json(404, {"error": "File tidak ditemukan"})
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # 1. Dashboard Web UI
        if self.path in ("/", "/admin", "/dashboard"):
            if DASHBOARD_HTML_PATH.exists():
                self._send_html(200, DASHBOARD_HTML_PATH.read_text(encoding="utf-8"))
            else:
                self._send_html(200, "<h1>Dashboard HTML tidak ditemukan</h1>")
            return

        # 2. Healthcheck
        if self.path.startswith("/health"):
            self._send_json(200, {
                "status": "online",
                "service": "Kasir Kulkas Webhook & Admin Dashboard",
                "mutasi_count": self.store.count(),
                "time": datetime.now().isoformat(),
            })
            return

        # 3. API Sesi Transaksi (dari transaksi.csv)
        if self.path.startswith("/api/sessions"):
            sessions = []
            if TRANSAKSI_CSV_PATH.exists():
                try:
                    with TRANSAKSI_CSV_PATH.open(encoding="utf-8", errors="replace") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            sessions.append(dict(row))
                    # Urutkan dari yang paling baru
                    sessions.reverse()
                except Exception as exc:
                    print(f"[webhook] Gagal baca transaksi.csv: {exc}")
            self._send_json(200, {"count": len(sessions), "sessions": sessions})
            return

        # 4. API Mutasi Masuk
        if self.path.startswith("/api/mutations") or self.path.startswith("/mutasi"):
            recent = self.store.list_recent(window_seconds=86400) # 24 jam terakhir
            recent.reverse()
            data = [
                {
                    "id": m.id,
                    "amount": m.amount,
                    "timestamp": m.timestamp.isoformat(),
                    "source": m.source,
                    "ref": m.ref,
                    "claimed": m.claimed,
                }
                for m in recent
            ]
            self._send_json(200, {"count": len(data), "mutasi": data})
            return

        # 5. Static Image Server untuk Capture Foto
        if self.path.startswith("/photos/"):
            filename = self.path.replace("/photos/", "").split("?")[0]
            # Mencegah path traversal (hanya ambil nama file)
            safe_name = Path(filename).name
            target = PELANGGARAN_DIR / safe_name
            ctype = mimetypes.guess_type(safe_name)[0] or "image/jpeg"
            self._send_file(target, ctype)
            return

        self._send_json(404, {"error": "Not Found"})

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace")

        try:
            payload = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        # 1. Endpoint Webhook Midtrans
        if self.path.startswith("/webhook/midtrans"):
            order_id = str(payload.get("order_id", ""))
            status_code = str(payload.get("status_code", ""))
            gross_amount = str(payload.get("gross_amount", "0"))
            signature_key = str(payload.get("signature_key", ""))
            tx_status = str(payload.get("transaction_status", "")).lower()
            fraud_status = str(payload.get("fraud_status", "")).lower()

            # Verifikasi signature jika server key dikonfigurasi
            sk = self.server_key or os.environ.get(ENV_SERVER_KEY, "")
            if sk and signature_key:
                if not verify_midtrans_signature(order_id, status_code, gross_amount, sk, signature_key):
                    print(f"[webhook] SIGNATURE TIDAK VALID untuk order: {order_id}")
                    self._send_json(403, {"error": "Invalid signature"})
                    return

            # Hanya simpan mutasi jika pembayaran sudah settlement / capture-accept
            lunas = tx_status == "settlement" or (tx_status == "capture" and fraud_status == "accept")
            if lunas:
                try:
                    nominal = int(float(gross_amount))
                except ValueError:
                    nominal = 0

                # Tanggal transaksi dari payload
                tx_time_str = payload.get("transaction_time") or payload.get("settlement_time")
                tx_dt = None
                if tx_time_str:
                    try:
                        tx_dt = datetime.strptime(tx_time_str, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass

                m = self.store.add(
                    m_id=order_id,
                    amount=nominal,
                    timestamp=tx_dt,
                    source="midtrans",
                    ref=order_id,
                    raw=payload,
                )
                print(f"[webhook] Mutasi Midtrans MASUK: {order_id} - Rp {nominal:,} (status: {tx_status})")
                self._send_json(200, {"status": "ok", "mutation_id": m.id, "amount": m.amount})
                return

            print(f"[webhook] Notifikasi Midtrans diterima tapi belum settlement: {order_id} ({tx_status})")
            self._send_json(200, {"status": "ignored", "reason": f"status {tx_status}"})
            return

        # 2. Endpoint Webhook Manual / Simulasi Bank
        if self.path.startswith("/webhook/manual") or self.path.startswith("/webhook/bank"):
            m_id = str(payload.get("id") or f"MANUAL-{datetime.now().strftime('%H%M%S')}")
            amount = int(payload.get("amount", 0))
            ref = str(payload.get("ref", "-"))
            source = str(payload.get("source", "manual"))
            if amount <= 0:
                self._send_json(400, {"error": "amount harus > 0"})
                return

            m = self.store.add(m_id=m_id, amount=amount, source=source, ref=ref, raw=payload)
            print(f"[webhook] Mutasi Manual MASUK: {m_id} - Rp {amount:,} (ref: {ref})")
            self._send_json(200, {"status": "ok", "mutation_id": m.id, "amount": m.amount})
            return

        self._send_json(404, {"error": "Not Found"})


def start_webhook_server(
    host: str = "0.0.0.0",
    port: int = 8091,
    store: MutationStore | None = None,
    server_key: str = "",
) -> ThreadedHTTPServer:
    """Mulai server webhook di background thread."""
    if store is not None:
        WebhookHandler.store = store
    if server_key:
        WebhookHandler.server_key = server_key
    else:
        WebhookHandler.server_key = os.environ.get(ENV_SERVER_KEY, "")

    server = ThreadedHTTPServer((host, port), WebhookHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="webhook-server")
    t.start()
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="Server Webhook Lokal Mutasi Kasir Kulkas")
    parser.add_argument("--port", type=int, default=8091, help="Port lokal HTTP (default: 8091)")
    parser.add_argument("--host", default="0.0.0.0", help="Host binding (default: 0.0.0.0)")
    parser.add_argument("--simulate", type=int, help="Kirim simulasi mutasi sebesar nominal ke server lokal")
    parser.add_argument("--ref", default="REF-SIMULASI-001", help="Nomor ref untuk simulasi")
    args = parser.parse_args()

    if args.simulate:
        import urllib.request
        url = f"http://127.0.0.1:{args.port}/webhook/manual"
        body = json.dumps({"amount": args.simulate, "ref": args.ref, "source": "simulasi"}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                print("Simulasi terkirim:", resp.read().decode("utf-8"))
                return 0
        except Exception as exc:
            print(f"Gagal mengirim simulasi ke {url}: {exc}")
            return 1

    print("==========================================================")
    print("      SERVER WEBHOOK MUTASI KASIR KULKAS (LOCAL)          ")
    print("==========================================================")
    print(f"Listening di   : http://{args.host}:{args.port}")
    print(f"Endpoint Midtrans: http://localhost:{args.port}/webhook/midtrans")
    print(f"Endpoint Manual  : http://localhost:{args.port}/webhook/manual")
    print("----------------------------------------------------------")
    print("CARA MENGHUBUNGKAN DARI INTERNET (Pilih salah satu):")
    print(" 1. Cloudflare Tunnel (Gratis & Rekomendasi):")
    print(f"    cloudflared tunnel --url http://localhost:{args.port}")
    print(" 2. ngrok:")
    print(f"    ngrok http {args.port}")
    print(" Lalu salin URL HTTPS ke Midtrans Dashboard -> Settings -> Configuration -> Payment Notification URL")
    print("==========================================================")
    print("Tekan Ctrl+C untuk berhenti.\n")

    store = MutationStore()
    server = start_webhook_server(args.host, args.port, store=store)
    try:
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        print("\n[webhook] Menghentikan server...")
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
