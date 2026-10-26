"""Kasir kulkas kejujuran: deteksi minuman + verifikasi bukti bayar (realtime webcam).

Alur:
  1. MINUMAN : tunjukkan minuman -> YOLO hitung item x harga menu
  2. BAYAR   : tunjukkan bukti bayar (layar HP) -> OCR nominal
  3. HASIL   : total <= dibayar => "Terima kasih", selain itu pelanggaran
               (foto disimpan + notifikasi Telegram kalau dikonfigurasi)

Kontrol: r = reset, s = simpan transaksi manual, spasi = paksa terima nominal, q = keluar.

Contoh:
  python src/kasir_kulkas.py                       # webcam, weights/multiclass_best.pt + menu.csv
  python src/kasir_kulkas.py --source video.mp4 --no-window --save out.mp4
  python src/kasir_kulkas.py --selftest            # uji OCR + deteksi tanpa kamera
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import threading
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ROOT, fmt_rupiah, letterbox, load_env, screen_size  # noqa: E402
from accelerate import load_detector, tune_threads  # noqa: E402
from md_remote import DEFAULT_URL as DEFAULT_MD_URL, MoondreamEngine  # noqa: E402
from midtrans_pay import MidtransGateway  # noqa: E402
from ocr_payment import PaddleEngine, find_amounts, preprocess  # noqa: E402
from reconcile import MutationStore, Reconciler, parse_receipt_datetime  # noqa: E402
from telegram_notify import TelegramBot  # noqa: E402
from webhook_server import start_webhook_server  # noqa: E402

REF_RE = re.compile(
    r"(?:ref|no\.?\s*ref|id transaksi|transaction id|no\.?\s*transaksi)[\s:.#]*([a-z0-9]{6,})",
    re.IGNORECASE,
)


def default_menu() -> Path:
    return ROOT / "menu.csv"


def default_model() -> Path:
    return ROOT / "weights" / "multiclass_best.pt"


def load_menu(path: Path) -> dict[str, dict]:
    menu: dict[str, dict] = {}
    if not path.exists():
        return menu
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            menu[row["kelas"].strip()] = {
                "nama": row["nama"].strip(),
                "harga": int(row["harga"]),
            }
    return menu


class WinEngine:
    """OCR bawaan Windows (WinRT). ~80 ms di CPU, tanpa model tambahan."""

    def __init__(self, lang: str = "en"):
        import asyncio

        import winocr

        self._asyncio = asyncio
        self._winocr = winocr
        self.lang = lang
        try:
            self.read(np.zeros((64, 64, 3), dtype=np.uint8))
        except Exception:  # noqa: BLE001 - warmup best effort
            pass

    def read(self, image) -> list[tuple[str, float, None]]:
        async def _go():
            return await self._winocr.recognize_cv2(image, lang=self.lang)

        res = self._asyncio.run(_go())
        return [(line.text, 1.0, None) for line in res.lines]


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if not inter:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    return inter / max(1, area_a + area_b - inter)


class KasirKulkas:
    FRAME_STABIL = 10
    TIMEOUT_MINUMAN = 60
    TIMEOUT_BAYAR = 60
    DURASI_HASIL = 6
    INTERVAL_OCR = 0.4

    def __init__(
        self, menu, model_path, conf, harga_default, log, folder,
        beep=True, stabil=10, imgsz=640, device="auto", det_every=1, ocr="auto",
        min_area=0.01, min_confirm=3, confirm_sec=0.35,
        detector="yolo", md_url="", md_prompt="bottle", md_label="bottle",
        md_resize=960, telegram=True, telegram_token="", telegram_chat="",
        midtrans=True, midtrans_server="", midtrans_client="", midtrans_merchant="",
        webhook=True, webhook_port=8091, tolerance_sec=180, require_mutation=False,
        timeout_bayar=60,
    ):
        tune_threads()
        self.TIMEOUT_BAYAR = max(5, timeout_bayar)
        self.menu = menu
        self.detector = detector
        self.md: MoondreamEngine | None = None
        self.md_ms = 0.0
        url = md_url or os.environ.get("MOONDREAM_URL", "")
        if detector in ("auto", "moondream") and url:
            eng = MoondreamEngine(url, prompt=md_prompt, label=md_label, resize=md_resize)
            if eng.health():
                self.md = eng
            elif detector == "moondream":
                raise SystemExit(
                    f"[kasir] server moondream {url} tidak merespon: {eng.last_error}"
                )
        if self.md is not None:
            self.model, self.device_arg = None, "http"
            self.names = {0: md_label}
            print(
                f"[kasir] detektor moondream {self.md.url} "
                f"prompt='{md_prompt}' -> {md_label}"
            )
        else:
            self.model, self.device_arg = load_detector(model_path, imgsz=imgsz, device=device)
            self.names = self.model.names if isinstance(self.model.names, dict) else {}
            print(f"[kasir] detektor YOLO {model_path} ({len(self.names)} kelas)")
        self.imgsz = imgsz
        self.det_every = max(1, det_every)
        self.min_area = max(0.0, min_area)
        self.min_confirm = max(1, min_confirm)
        self.confirm_sec = max(0.0, confirm_sec)
        self._tick = 0
        self._cache = []
        self.tracks: list[dict] = []
        self.kandidat: list[tuple[int, int, int, int]] = []
        self.fps = 0.0
        self.conf = conf
        self.harga_default = harga_default
        self.log = Path(log)
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.beep = beep
        self.FRAME_STABIL = max(3, stabil)
        self.telegram = TelegramBot(telegram_token, telegram_chat) if telegram else None
        if self.telegram is not None and self.telegram.enabled:
            print("[kasir] notifikasi pelanggaran -> Telegram aktif")
        elif self.telegram is not None:
            print(
                "[kasir] telegram belum dikonfigurasi "
                "(set TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID)"
            )
        self.midtrans = (
            MidtransGateway(
                server_key=midtrans_server,
                client_key=midtrans_client,
                merchant_id=midtrans_merchant,
            )
            if midtrans
            else None
        )
        if self.midtrans is not None and self.midtrans.enabled:
            mode = "PROD" if self.midtrans.is_production else "SANDBOX"
            print(f"[kasir] pembayaran Midtrans ({mode}) aktif")
        elif self.midtrans is not None:
            print("[kasir] midtrans belum dikonfigurasi (set MIDTRANS_SERVER_KEY)")
        self.midtrans_order_id = ""
        self.midtrans_qr = None
        self.midtrans_paid = False
        self.midtrans_poll_gen = 0

        self.mutation_store = MutationStore()
        self.reconciler = Reconciler(
            store=self.mutation_store,
            tolerance_seconds=tolerance_sec,
            require_mutation=require_mutation,
        )
        self.webhook_server = None
        if webhook:
            try:
                self.webhook_server = start_webhook_server(
                    port=webhook_port, store=self.mutation_store
                )
                print(f"[kasir] webhook mutasi lokal aktif di http://localhost:{webhook_port}")
            except Exception as exc:
                print(f"[kasir] webhook server gagal di port {webhook_port}: {exc}")
        print(f"[kasir] menu {len(menu)} item")

        self.ocr_backend = ocr
        self.engine: object | None = None
        self.ocr_name = "-"
        self._engine_lock = threading.Lock()
        self._ocr_thread: threading.Thread | None = None
        self._ocr_result: dict | None = None
        self._ocr_last = 0.0
        self._ocr_gen = 0
        self.last_amount: int | None = None
        self.reset()

    # ---------------- util ----------------
    def reset(self, pesan=""):
        self.state = "MINUMAN"
        self.dibayar = 0
        self.ref = "-"
        self.t_state = time.time()
        self.riwayat: list[Counter] = []
        self.hitung: Counter = Counter()
        self.total_minuman = 0
        self.frame_minuman = None
        self.frame_struk = None
        self.pesan = pesan
        self.pesan2 = ""
        self.warna: tuple[int, int, int] = (255, 255, 255)
        self.pelanggaran = False
        self.tracks = []
        self.kandidat = []
        self._cache = []
        self._tick = 0
        self._ocr_result = None
        self._ocr_last = 0.0
        self._ocr_gen += 1
        self.midtrans_order_id = ""
        self.midtrans_qr = None
        self.midtrans_paid = False
        self.midtrans_poll_gen += 1

    def _bunyi(self):
        if not self.beep:
            return

        def _b():
            try:
                import winsound

                for _ in range(2):
                    winsound.Beep(1300, 250)
            except Exception:
                print("\a", end="", flush=True)

        threading.Thread(target=_b, daemon=True).start()

    def _catat(self, minuman: str, total: int, status: str, foto: str = "", foto_minuman: str = "", foto_pelanggaran: str = ""):
        baru = not self.log.exists()
        now_dt = datetime.now()
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        with self.log.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if baru:
                w.writerow(
                    ["waktu", "referensi", "dibayar", "minuman", "harga_minuman", "status", "foto", "foto_minuman", "foto_pelanggaran"]
                )
            w.writerow(
                [
                    now_str,
                    self.ref,
                    self.dibayar,
                    minuman,
                    total,
                    status,
                    foto,
                    foto_minuman,
                    foto_pelanggaran,
                ]
            )

        # Catat juga ke SQLite database kulkas.db secara asinkron/aman
        try:
            from db import record_transaction

            reason = ""
            if "PELANGGARAN" in status:
                parts = status.split(":", 1)
                reason = parts[1].strip() if len(parts) > 1 else "PELANGGARAN"
            tx_id = f"TX_{now_dt.strftime('%Y%m%d_%H%M%S')}"
            record_transaction(
                tx_id=tx_id,
                timestamp=now_str,
                total_amount=total,
                paid_amount=self.dibayar,
                status=status,
                fraud_reason=reason,
                reference_no=self.ref,
                photo_full_path=foto_pelanggaran or foto,
                photo_item_path=foto_minuman,
            )
        except Exception as exc:
            pass

    def _ocr(self, frame, gen):
        try:
            data = self._baca_bukti(frame)
        except Exception as exc:  # noqa: BLE001
            print(f"[kasir] OCR error: {exc}")
            data = {}
        if gen == self._ocr_gen:
            self._ocr_result = data

    def preload_ocr(self):
        with self._engine_lock:
            if self.engine is not None:
                return
            if self.ocr_backend in ("auto", "winocr"):
                try:
                    self.engine, self.ocr_name = WinEngine("en"), "winocr"
                except Exception as exc:  # noqa: BLE001
                    if self.ocr_backend == "winocr":
                        raise
                    print(f"[kasir] winocr tidak tersedia ({exc}); pakai PaddleOCR")
            if self.engine is None:
                self.engine, self.ocr_name = PaddleEngine(lang="en"), "paddle"
            self.INTERVAL_OCR = 0.4 if self.ocr_name == "winocr" else 1.2
            print(f"[kasir] OCR: {self.ocr_name} (interval {self.INTERVAL_OCR}s)")

    def _baca_bukti(self, frame) -> dict:
        if self.engine is None:
            self.preload_ocr()
        name = self.ocr_name
        h, w = frame.shape[:2]
        if w > 800:
            frame = cv2.resize(frame, (800, int(h * 800 / w)), interpolation=cv2.INTER_AREA)
        if name == "winocr":
            items = self.engine.read(frame)  # type: ignore[union-attr]
        else:
            proc, _ = preprocess(frame, "clahe")
            items = self.engine.read(proc)  # type: ignore[union-attr]
        texts = [t for t, s, _ in items]
        jumlah = find_amounts(texts)
        nominal = max(jumlah) if jumlah else None
        status = "BERHASIL" if nominal else "TIDAK DIKETAHUI"
        ref = REF_RE.search(" ".join(texts))
        waktu_struk = parse_receipt_datetime(texts)
        return {
            "jumlah": nominal,
            "status": status,
            "referensi": ref.group(1).upper() if ref else "-",
            "waktu_struk": waktu_struk,
            "teks": " | ".join(texts),
        }

    def _deteksi_mentah(self, frame) -> list[tuple[int, int, int, int, float, int]]:
        """Deteksi + filter geometri (area/aspect) sebelum tracking."""
        mentah: list[tuple[float, float, float, float, float, int]] = []
        if self.md is not None:
            mentah = self.md.detect(frame)  # type: ignore[assignment]
            self.md_ms = self.md.last_ms
        else:
            res = self.model.predict(  # type: ignore[union-attr]
                frame,
                conf=self.conf,
                imgsz=self.imgsz,
                device=self.device_arg,
                max_det=30,
                verbose=False,
            )[0]
            for box in res.boxes:
                x1, y1, x2, y2 = map(float, box.xyxy[0])
                mentah.append((x1, y1, x2, y2, float(box.conf[0]), int(box.cls[0])))
        h, w = frame.shape[:2]
        hasil = []
        for x1, y1, x2, y2, conf, cls_id in mentah:
            bw, bh = (x2 - x1) / w, (y2 - y1) / h
            area = bw * bh
            ar = bw / max(bh, 1e-6)
            if area < self.min_area or area > 0.85 or not 0.08 <= ar <= 6.0:
                continue
            hasil.append((int(x1), int(y1), int(x2), int(y2), float(conf), int(cls_id)))
        return hasil

    def _update_tracks(self, mentah):
        """Hanya objek yang bertahan (IoU antar frame) yang dianggap nyata."""
        now = time.time()
        lama = len(self.tracks)
        dipakai = [False] * lama
        self.kandidat = []
        for x1, y1, x2, y2, conf, cls_id in mentah:
            idx, iou_max = -1, 0.3
            for i in range(lama):
                tr = self.tracks[i]
                if dipakai[i] or tr["cls"] != cls_id:
                    continue
                iou = _iou(tr["box"], (x1, y1, x2, y2))
                if iou > iou_max:
                    idx, iou_max = i, iou
            if idx >= 0:
                dipakai[idx] = True
                tr = self.tracks[idx]
                tr.update(box=(x1, y1, x2, y2), conf=conf, hits=tr["hits"] + 1, last=now)
            else:
                self.tracks.append(
                    {"box": (x1, y1, x2, y2), "conf": conf, "cls": cls_id,
                     "hits": 1, "first": now, "last": now, "ok": False}
                )
        self.tracks = [t for t in self.tracks if now - t["last"] <= 0.8]
        for tr in self.tracks:
            tr["ok"] = (
                tr["hits"] >= self.min_confirm
                and (now - tr["first"]) >= self.confirm_sec
            )
            if not tr["ok"]:
                self.kandidat.append(tr["box"])

    def _deteksi(self, frame):
        self._tick += 1
        if self._cache and self.det_every > 1 and self._tick % self.det_every:
            return self._cache
        self._update_tracks(self._deteksi_mentah(frame))
        deteksi = []
        for tr in self.tracks:
            if not tr["ok"]:
                continue
            kelas = self.names.get(tr["cls"], str(tr["cls"]))
            info = self.menu.get(kelas, {"nama": kelas, "harga": self.harga_default})
            deteksi.append((kelas, info["nama"], info["harga"], tr["conf"], tr["box"]))
            if len(deteksi) >= 20:
                break
        self._cache = deteksi
        return deteksi

    # ---------------- state ----------------
    def _tahap_minuman(self, frame):
        deteksi = self._deteksi(frame)
        total = sum(d[2] for d in deteksi)
        hitung = Counter(d[0] for d in deteksi)
        self.riwayat = (self.riwayat + [hitung])[-self.FRAME_STABIL:]
        stabil, pasti = self._stabil()
        if stabil:
            self.hitung = pasti
            self.total_minuman = sum(
                self.menu.get(k, {"harga": self.harga_default})["harga"] * n
                for k, n in pasti.items()
            )
            self.frame_minuman = frame.copy()
            self.state, self.t_state = "BAYAR", time.time()
            if self.midtrans and self.midtrans.enabled and self.total_minuman > 0:
                self.pesan = f"Total {fmt_rupiah(self.total_minuman)} - Scan QR / tunjukkan bukti"
                self.pesan2 = "Scan QR di layar atau tunjukkan bukti bayar ke kamera"
                self._mulai_midtrans(self.total_minuman, pasti)
            else:
                self.pesan = f"Total {fmt_rupiah(self.total_minuman)} - tunjukkan BUKTI BAYAR"
                self.pesan2 = "scan QRIS lalu tunjukkan struk ke kamera"
            self.warna = (255, 255, 255)
            print(
                f"[kasir] minuman {dict(pasti)} "
                f"total={fmt_rupiah(self.total_minuman)}"
            )
        elif time.time() - self.t_state > self.TIMEOUT_MINUMAN:
            # Saat idle / tidak ada minuman yang ditunjukkan, tidak perlu dicatat sebagai transaksi TIMEOUT di dashboard
            self.reset("Standby")
        return deteksi, total

    def _mulai_midtrans(self, total: int, hitung: Counter):
        if self.midtrans is None or not self.midtrans.enabled or total <= 0:
            return
        self.midtrans_poll_gen += 1
        gen = self.midtrans_poll_gen
        order_id = f"KASIR-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
        self.midtrans_order_id = order_id
        self.midtrans_qr = None
        self.midtrans_paid = False

        def _worker():
            items = []
            for k, count in hitung.items():
                info = self.menu.get(k, {"nama": k, "harga": self.harga_default})
                items.append({
                    "id": str(k)[:20],
                    "price": int(info["harga"]),
                    "quantity": int(count),
                    "name": str(info["nama"])[:50],
                })
            res = self.midtrans.create_snap_transaction(order_id, total, item_details=items)
            if not res or gen != self.midtrans_poll_gen:
                return
            redirect_url = res.get("redirect_url")
            if redirect_url:
                qr = self.midtrans.generate_qr_image(redirect_url, size=180, border=4)
                if gen == self.midtrans_poll_gen:
                    self.midtrans_qr = qr
                    print(f"[kasir] midtrans snap siap: {order_id} -> {redirect_url}")

            # Polling status sampai bayar / state berubah
            while gen == self.midtrans_poll_gen and self.state == "BAYAR":
                time.sleep(1.5)
                st = self.midtrans.check_status(order_id)
                if st and self.midtrans.is_settled(st):
                    print(f"[kasir] pembayaran Midtrans lunas: {order_id}")
                    # Catat mutasi masuk ke MutationStore kasir
                    self.mutation_store.add(
                        m_id=order_id,
                        amount=total,
                        timestamp=datetime.now(),
                        source="midtrans",
                        ref=order_id,
                    )
                    self.midtrans_paid = True
                    self.pesan = "Uang Midtrans masuk! Tunjukkan STRUK HP"
                    self.pesan2 = "Hadapkan layar struk HP ke kamera untuk verifikasi"
                    self.warna = (0, 235, 255)
                    break

        threading.Thread(target=_worker, daemon=True, name="midtrans").start()

    def _tahap_bayar(self, frame):
        # Catatan: Kasir tetap menunggu pembeli menghadapkan layar HP (bukti transfer) ke kamera
        # agar rekonsiliasi timestamp struk vs mutasi berjalan & foto bukti tersimpan.
        sibuk = self._ocr_thread is not None and self._ocr_thread.is_alive()
        if not sibuk and self._ocr_result is not None:
            data, self._ocr_result = self._ocr_result, None
            jumlah = data.get("jumlah")
            self.last_amount = jumlah or self.last_amount
            if jumlah:
                self.dibayar = jumlah
                self.ref = data.get("referensi", "-")
                self.frame_struk = frame.copy()
                waktu_struk = data.get("waktu_struk")
                now = datetime.now()
                is_ok, reason, detail = self.reconciler.verify(
                    nominal_ocr=jumlah,
                    total_tagihan=self.total_minuman,
                    scan_time=now,
                    receipt_time=waktu_struk,
                    referensi=self.ref,
                )
                if is_ok:
                    print(f"[kasir] bayar {fmt_rupiah(self.dibayar)} ref={self.ref} ({reason})")
                    self._evaluasi(frame, self.hitung, self.frame_minuman, kategori=reason)
                else:
                    print(f"[kasir] REKONSILIASI GAGAL: {reason} | detail={detail}")
                    self._evaluasi(frame, self.hitung, self.frame_minuman, alasan=reason)
                return
            if self.midtrans_paid:
                self.pesan = "Uang Midtrans masuk! Tunjukkan STRUK HP"
                self.pesan2 = "Dekatkan layar HP struk pembayaran ke kamera"
                self.warna = (0, 235, 255)
            else:
                self.pesan = "Nominal pembayaran belum terbaca"
                self.pesan2 = "Dekatkan HP / tekan spasi untuk paksa terima"
                self.warna = (0, 255, 255)
        elif not sibuk and time.time() - self._ocr_last > self.INTERVAL_OCR:
            self._ocr_last = time.time()
            self._ocr_thread = threading.Thread(
                target=self._ocr, args=(frame.copy(), self._ocr_gen), daemon=True
            )
            self._ocr_thread.start()
        if self.state == "BAYAR" and time.time() - self.t_state > self.TIMEOUT_BAYAR:
            print(f"[kasir] batas waktu {self.TIMEOUT_BAYAR}s habis; bukti bayar tidak ditunjukkan -> PELANGGARAN")
            self.dibayar = 0
            self._evaluasi(frame, self.hitung, self.frame_minuman, alasan="TIMEOUT_BAYAR_EXPIRED")

    def _stabil(self) -> tuple[bool, Counter]:
        """Suara mayoritas: kelas sama & jumlah tiap kelas tidak goyah >1 tiap frame."""
        if len(self.riwayat) < self.FRAME_STABIL or not self.riwayat[-1]:
            return False, Counter()
        kelas = set(self.riwayat[-1])
        if any(set(r) != kelas for r in self.riwayat):
            return False, Counter()
        pasti = Counter()
        for k in kelas:
            nilai = sorted(r[k] for r in self.riwayat)
            median = nilai[len(nilai) // 2]
            if all(abs(r[k] - median) <= 1 for r in self.riwayat):
                pasti[k] = median
            else:
                return False, Counter()
        return bool(pasti), pasti

    def _evaluasi(self, frame, hitung: Counter, frame_minuman=None, alasan: str = "", kategori: str = "OK"):
        rincian = []
        total = 0
        for kelas, n in hitung.items():
            info = self.menu.get(kelas, {"nama": kelas, "harga": self.harga_default})
            rincian.append((info["nama"], n))
            total += info["harga"] * n
        daftar = ", ".join(f"{n}x {nama}" for nama, n in rincian) or "-"
        self.total_minuman = total
        self.state, self.t_state = "HASIL", time.time()
        is_pelanggaran = (total > self.dibayar) or bool(alasan)
        if is_pelanggaran:
            self.pelanggaran = True
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            # 1. Foto Minuman
            foto_minuman = None
            if frame_minuman is not None:
                foto_minuman = self.folder / f"{ts}_minuman.jpg"
                cv2.imwrite(str(foto_minuman), frame_minuman)

            # 2. Foto Scan Bukti TF (Layar HP struk saat dihadapkan ke kamera)
            foto_struk = None
            frame_bukti = getattr(self, "frame_struk", None)
            if frame_bukti is not None:
                foto_struk = self.folder / f"{ts}_struk.jpg"
                cv2.imwrite(str(foto_struk), frame_bukti)

            # 3. Foto Pelanggaran (Kamera kasir saat alarm pelanggaran aktif: orang / pelaku)
            foto_pelanggaran = self.folder / f"{ts}_pelanggaran.jpg"
            cv2.imwrite(str(foto_pelanggaran), frame)

            if alasan:
                label_alasan = alasan.replace("PELANGGARAN_", "").replace("_", " ")
                self.pesan = f"PERINGATAN! {label_alasan[:35]}"
                self.pesan2 = f"Total {fmt_rupiah(total)} - foto disimpan"
            else:
                self.pesan = f"PERINGATAN! Minuman {fmt_rupiah(total)} > dibayar {fmt_rupiah(self.dibayar)}"
                self.pesan2 = f"Kurang {fmt_rupiah(total - self.dibayar)} - foto disimpan"
            self.warna = (0, 0, 255)
            self._bunyi()
            status_log = f"PELANGGARAN: {alasan}" if alasan else "PELANGGARAN"
            print(f"[kasir] {status_log} {daftar} total={fmt_rupiah(total)} -> {foto_pelanggaran}")
            self._catat(
                daftar, total, status_log,
                foto=str(foto_struk) if foto_struk else "",
                foto_minuman=str(foto_minuman) if foto_minuman else "",
                foto_pelanggaran=str(foto_pelanggaran),
            )
            self._kirim_telegram(
                str(foto_pelanggaran),
                str(foto_struk) if foto_struk else str(foto_pelanggaran),
                daftar, total, max(0, total - self.dibayar),
                catatan=f"Alasan: {alasan}" if alasan else ""
            )
        else:
            lebih = self.dibayar - total
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            foto_minuman = None
            if frame_minuman is not None:
                foto_minuman = self.folder / f"{ts}_ok_minuman.jpg"
                cv2.imwrite(str(foto_minuman), frame_minuman)

            frame_struk = getattr(self, "frame_struk", None)
            frame_bukti = frame_struk if frame_struk is not None else frame
            foto_struk = self.folder / f"{ts}_ok_struk.jpg"
            cv2.imwrite(str(foto_struk), frame_bukti)

            if kategori == "OK_REKONSILIASI_MUTASI":
                status_catat = "LUNAS: TERVERIFIKASI MUTASI"
                self.pesan = f"Terima Kasih! {daftar}"
                self.pesan2 = f"Pembayaran cocok! Lebih {fmt_rupiah(lebih)}" if lebih else "Pembayaran pas! Silakan ambil"
                self.warna = (0, 255, 0)
            elif kategori == "MANUAL_PAKSA":
                status_catat = "MANUAL: PAKSA TERIMA"
                self.pesan = f"Diterima Manual! {daftar}"
                self.pesan2 = "Pembayaran diterima manual kasir"
                self.warna = (0, 255, 255)
            else:
                status_catat = "PENDING: MENUNGGU MUTASI"
                # Di layar demo program GUI pembeli tidak dibingungkan menunggu mutasi; langsung sukses hijau
                self.pesan = f"Terima Kasih! {daftar}"
                self.pesan2 = f"Pembayaran diterima! Lebih {fmt_rupiah(lebih)}" if lebih else "Pembayaran diterima pas! Silakan ambil"
                self.warna = (0, 255, 0)

            print(f"[kasir] {status_catat} {daftar} total={fmt_rupiah(total)} -> {foto_struk}")
            self._catat(
                daftar, total, status_catat,
                foto=str(foto_struk),
                foto_minuman=str(foto_minuman) if foto_minuman else "",
                foto_pelanggaran="",
            )

    def _kirim_telegram(self, foto, foto_bayar, daftar, total, kurang, catatan: str = ""):
        bot = self.telegram
        if bot is None or not bot.enabled:
            return
        dibayar, referensi = self.dibayar, self.ref

        def _go():
            ok = bot.notify_violation(
                foto,
                total=total,
                dibayar=dibayar,
                minuman=daftar,
                referensi=referensi,
                kurang=kurang,
                catatan=catatan,
            )
            if not ok:
                print(f"[kasir] telegram gagal: {bot.last_error}")
                return
            print("[kasir] notifikasi pelanggaran terkirim ke Telegram")
            if Path(foto_bayar) != Path(foto) and Path(foto_bayar).exists():
                bot.send_photo(foto_bayar, "Bukti bayar saat pelanggaran")

        threading.Thread(target=_go, daemon=True, name="telegram").start()

    def _gambar(self, frame, deteksi, total):
        tampil = frame.copy()
        for x1, y1, x2, y2 in self.kandidat:
            cv2.rectangle(tampil, (x1, y1), (x2, y2), (130, 130, 130), 1)
        for _, nama, harga, conf, (x1, y1, x2, y2) in deteksi:
            cv2.rectangle(tampil, (x1, y1), (x2, y2), (0, 220, 90), 2)
            cv2.putText(
                tampil,
                f"{nama} {fmt_rupiah(harga)} {conf:.2f}",
                (x1, max(18, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 220, 90),
                2,
                cv2.LINE_AA,
            )

        h, w = tampil.shape[:2]
        cv2.rectangle(tampil, (0, 0), (w, 78), (20, 20, 20), -1)
        if self.state == "MINUMAN":
            judul = "1. Tunjukkan MINUMAN ke kamera"
            sub = "tahan sampai terbaca stabil"
            if self.kandidat and not deteksi:
                sub = f"{len(self.kandidat)} kandidat, menunggu konfirmasi..."
        elif self.state == "BAYAR":
            sisa_sec = max(0.0, self.TIMEOUT_BAYAR - (time.time() - self.t_state))
            judul = (
                f"2. Total {fmt_rupiah(self.total_minuman)} - "
                f"BUKTI BAYAR [Sisa: {int(sisa_sec)}s]"
            )
            if sisa_sec <= 15:
                sub = f"PERINGATAN! Waktu habis dlm {int(sisa_sec)}s -> PELANGGARAN!"
                self.warna = (0, 0, 255) if int(time.time() * 3) % 2 == 0 else (0, 140, 255)
            elif sisa_sec <= 30:
                sub = f"Sisa waktu bayar: {int(sisa_sec)}s sebelum dicatat pelanggaran"
                self.warna = (0, 180, 255)
            else:
                sub = self.pesan or "Scan QRIS Midtrans / hadapkan struk ke kamera"
            if self.engine is None:
                sub = "OCR menyiapkan... " + sub
        else:
            judul = self.pesan
            sub = self.pesan2
        cv2.putText(tampil, judul[:80], (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                    self.warna if self.state == "HASIL" else (255, 255, 255), 2)
        cv2.putText(tampil, sub[:90], (10, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.55, self.warna, 1)

        # Progress bar countdown expirasi di bawah header atas (y: 74-82) saat tahap BAYAR
        if self.state == "BAYAR":
            sisa_sec = max(0.0, self.TIMEOUT_BAYAR - (time.time() - self.t_state))
            pct = max(0.0, min(1.0, sisa_sec / self.TIMEOUT_BAYAR))
            bar_w = int(w * pct)
            if sisa_sec > 30:
                bar_color = (0, 220, 90)
            elif sisa_sec > 15:
                bar_color = (0, 215, 255)
            else:
                bar_color = (0, 0, 255) if int(time.time() * 4) % 2 == 0 else (0, 50, 220)

            cv2.rectangle(tampil, (0, 74), (w, 82), (35, 35, 35), -1)
            if bar_w > 0:
                cv2.rectangle(tampil, (0, 74), (bar_w, 82), bar_color, -1)
            cv2.putText(
                tampil,
                f"Expirasi: {int(sisa_sec)}s",
                (w - 145, 70),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                bar_color,
                1,
                cv2.LINE_AA,
            )
        cv2.putText(
            tampil, f"{self.fps:4.1f} FPS", (w - 120, 30),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 235, 255), 2,
        )
        if self.md is not None:
            cv2.putText(
                tampil, f"MD {self.md_ms:3.0f}ms", (w - 120, 52),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 255), 1,
            )

        if self.state == "BAYAR" and self.midtrans_qr is not None:
            qh, qw = self.midtrans_qr.shape[:2]
            top = 84
            right = w - qw - 20
            if right > 0 and top + qh < h - 60:
                cv2.rectangle(
                    tampil,
                    (right - 8, top - 24),
                    (right + qw + 8, top + qh + 20),
                    (25, 25, 25),
                    -1,
                )
                cv2.rectangle(
                    tampil,
                    (right - 8, top - 24),
                    (right + qw + 8, top + qh + 20),
                    (0, 235, 255),
                    2,
                )
                cv2.putText(
                    tampil,
                    "SCAN QR UNTUK BAYAR",
                    (right - 2, top - 8),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.42,
                    (0, 235, 255),
                    1,
                    cv2.LINE_AA,
                )
                tampil[top : top + qh, right : right + qw] = self.midtrans_qr
                cv2.putText(
                    tampil,
                    "Midtrans Sandbox QRIS",
                    (right + 4, top + qh + 14),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.38,
                    (200, 200, 200),
                    1,
                    cv2.LINE_AA,
                )

        cv2.rectangle(tampil, (0, h - 58), (w, h), (20, 20, 20), -1)
        kiri = f"Total: {fmt_rupiah(total)}"
        kanan = f"Dibayar: {fmt_rupiah(self.dibayar) if self.dibayar else '-'}   ref {self.ref}"
        if self.state == "BAYAR":
            sisa_sec = max(0.0, self.TIMEOUT_BAYAR - (time.time() - self.t_state))
            kanan += f"   |   Batas Waktu: {int(sisa_sec)}s"
        cv2.putText(tampil, kiri, (10, h - 34), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 235, 255), 2)
        cv2.putText(tampil, kanan, (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (200, 200, 200), 1)
        cv2.putText(tampil, "r=reset  spasi=paksa  s=simpan  q=keluar", (w - 330, h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (140, 140, 140), 1)
        if self.pelanggaran and int(time.time() * 4) % 2:
            cv2.rectangle(tampil, (0, 0), (w - 1, h - 1), (0, 0, 255), 18)
        return tampil

    def step(self, frame):
        deteksi, total = [], 0
        if self.state == "MINUMAN":
            deteksi, total = self._tahap_minuman(frame)
        elif self.state == "BAYAR":
            self._tahap_bayar(frame)
            total = self.total_minuman
        else:
            total = self.total_minuman
            if time.time() - self.t_state > self.DURASI_HASIL:
                self.reset()
        return deteksi, total


def selftest(args) -> int:
    menu = load_menu(Path(args.menu))
    model_path = Path(args.model)
    print(f"[selftest] model  : {model_path}")
    print(f"[selftest] menu   : {Path(args.menu)} ({len(menu)} item)")
    kasir = KasirKulkas(menu, model_path, args.conf, args.harga_default, args.log, args.folder, beep=False)

    bukti = ROOT / "build" / "ocr_selftest.png"
    if bukti.exists():
        data = kasir._baca_bukti(cv2.imread(str(bukti)))
        print(f"[selftest] OCR    : {data['status']} {fmt_rupiah(data['jumlah'] or 0)} | {data['teks'][:70]}")
        if data["status"] != "BERHASIL" or data["jumlah"] != 250000:
            print("[selftest] GAGAL: OCR tidak sesuai (harus BERHASIL Rp 250.000)")
            return 1
    else:
        print(f"[selftest] (lewati OCR, {bukti} tidak ada)")

    gambar = sorted((ROOT / "dataset" / "images" / "val").glob("*.jpg"))
    if gambar:
        mentah = kasir._deteksi_mentah(cv2.imread(str(gambar[0])))
        deteksi = []
        for x1, y1, x2, y2, conf, cls_id in mentah:
            kelas = kasir.names.get(cls_id, str(cls_id))
            info = kasir.menu.get(kelas, {"nama": kelas, "harga": kasir.harga_default})
            deteksi.append((kelas, info["nama"], info["harga"], conf, (x1, y1, x2, y2)))
        total = sum(d[2] for d in deteksi)
        print(f"[selftest] deteksi: {len(deteksi)} objek, total {fmt_rupiah(total)}")
        if not deteksi:
            print("[selftest] GAGAL: model tidak mendeteksi apa pun")
            return 1
    print("[selftest] OK")
    return 0


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description="Kasir kulkas: deteksi minuman + verifikasi bayar")
    ap.add_argument("--source", default="0", help="index webcam atau path video")
    ap.add_argument("--model", default=str(default_model()))
    ap.add_argument("--menu", default=str(default_menu()))
    ap.add_argument("--conf", type=float, default=0.45)
    ap.add_argument("--harga-default", type=int, default=5000)
    ap.add_argument("--log", default="transaksi.csv")
    ap.add_argument("--folder", default="pelanggaran")
    ap.add_argument(
        "--pay-image", default="",
        help="pra-isi bukti bayar dari gambar; dipakai otomatis setelah minuman",
    )
    ap.add_argument("--save", default="", help="rekam tampilan ke .mp4")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--stabil", type=int, default=10, help="jumlah frame stabil sebelum menilai")
    ap.add_argument("--imgsz", type=int, default=640, help="ukuran inference YOLO")
    ap.add_argument(
        "--device", default="auto",
        help="auto (OpenVINO GPU) | intel:gpu | intel:cpu | cpu | cuda",
    )
    ap.add_argument("--det-every", type=int, default=1, help="deteksi tiap N frame (>1 hemat CPU)")
    ap.add_argument("--ocr", default="auto", choices=["auto", "winocr", "paddle"])
    ap.add_argument("--min-area", type=float, default=0.01, help="area kotak minimum (fraksi frame)")
    ap.add_argument("--min-confirm", type=int, default=3, help="minimal kemunculan sebelum dihitung")
    ap.add_argument("--confirm-sec", type=float, default=0.35, help="lama objek harus bertahan (detik)")
    ap.add_argument("--timeout-bayar", type=int, default=60, help="batas waktu bayar (detik) sebelum pelanggaran")
    ap.add_argument("--cam-width", type=int, default=960)
    ap.add_argument("--cam-height", type=int, default=540)
    ap.add_argument(
        "--detector", default="auto", choices=["auto", "moondream", "yolo"],
        help="auto: moondream kalau server hidup, kalau tidak YOLO lokal",
    )
    ap.add_argument("--md-url", default=os.environ.get("MOONDREAM_URL", DEFAULT_MD_URL))
    ap.add_argument("--md-prompt", default="bottle", help="prompt teks untuk moondream")
    ap.add_argument("--md-label", default="bottle", help="kelas menu untuk kotak moondream")
    ap.add_argument("--md-resize", type=int, default=960, help="sisi maks gambar ke server")
    ap.add_argument("--no-beep", action="store_true")
    ap.add_argument("--no-telegram", action="store_true", help="matikan notifikasi pelanggaran")
    ap.add_argument(
        "--telegram-token", default=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        help="token bot Telegram (default dari env)",
    )
    ap.add_argument(
        "--telegram-chat", default=os.environ.get("TELEGRAM_CHAT_ID", ""),
        help="chat id tujuan notifikasi (default dari env)",
    )
    ap.add_argument("--telegram-test", action="store_true", help="kirim pesan uji lalu keluar")
    ap.add_argument("--no-midtrans", action="store_true", help="matikan pembayaran online Midtrans")
    ap.add_argument(
        "--midtrans-server", default=os.environ.get("MIDTRANS_SERVER_KEY", ""),
        help="Midtrans Server Key (default dari env)",
    )
    ap.add_argument(
        "--midtrans-client", default=os.environ.get("MIDTRANS_CLIENT_KEY", ""),
        help="Midtrans Client Key (default dari env)",
    )
    ap.add_argument(
        "--midtrans-merchant", default=os.environ.get("MIDTRANS_MERCHANT_ID", ""),
        help="Midtrans Merchant ID (default dari env)",
    )
    ap.add_argument("--midtrans-test", action="store_true", help="uji koneksi Midtrans lalu keluar")
    ap.add_argument("--no-webhook", action="store_true", help="matikan server webhook lokal")
    ap.add_argument("--webhook-port", type=int, default=8091, help="port webhook lokal (default: 8091)")
    ap.add_argument("--require-mutation", action="store_true", help="wajibkan mutasi rekening masuk sebelum lunas")
    ap.add_argument("--tolerance-sec", type=int, default=180, help="toleransi selisih waktu (detik, default: 180)")
    ap.add_argument("--fullscreen", action="store_true", help="tampilkan layar penuh (letterbox)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.telegram_test:
        bot = TelegramBot(args.telegram_token, args.telegram_chat)
        if not bot.enabled:
            print("[kasir] telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diisi")
            return 1
        ok = bot.send_message("Kasir Kulkas: tes notifikasi Telegram berhasil.")
        print("[kasir] telegram tes:", "terkirim" if ok else f"gagal - {bot.last_error}")
        return 0 if ok else 1

    if args.midtrans_test:
        gw = MidtransGateway(
            server_key=args.midtrans_server,
            client_key=args.midtrans_client,
            merchant_id=args.midtrans_merchant,
        )
        if not gw.enabled:
            print("[kasir] midtrans: MIDTRANS_SERVER_KEY belum diisi di .env")
            return 1
        print(f"[kasir] menguji Midtrans Snap ({'PROD' if gw.is_production else 'SANDBOX'})...")
        test_id = f"KASIR-TEST-{uuid.uuid4().hex[:6].upper()}"
        res = gw.create_snap_transaction(
            test_id, 10000, [{"id": "TEST", "price": 10000, "quantity": 1, "name": "Tes Transaksi"}]
        )
        if res and res.get("redirect_url"):
            print(f"[kasir] midtrans tes: BERHASIL -> {res['redirect_url']}")
            return 0
        print(f"[kasir] midtrans tes: GAGAL -> {gw.last_error}")
        return 1

    if args.selftest:
        return selftest(args)

    menu = load_menu(Path(args.menu))
    if not menu:
        print(f"[kasir] menu '{args.menu}' kosong/tidak ada; semua item pakai harga default")
    kasir = KasirKulkas(
        menu, Path(args.model), args.conf, args.harga_default, args.log, args.folder,
        beep=not args.no_beep,
        stabil=args.stabil,
        imgsz=args.imgsz,
        device=args.device,
        det_every=args.det_every,
        ocr=args.ocr,
        min_area=args.min_area,
        min_confirm=args.min_confirm,
        confirm_sec=args.confirm_sec,
        detector=args.detector,
        md_url=args.md_url,
        md_prompt=args.md_prompt,
        md_label=args.md_label,
        md_resize=args.md_resize,
        telegram=not args.no_telegram,
        telegram_token=args.telegram_token,
        telegram_chat=args.telegram_chat,
        midtrans=not args.no_midtrans,
        midtrans_server=args.midtrans_server,
        midtrans_client=args.midtrans_client,
        midtrans_merchant=args.midtrans_merchant,
        webhook=not args.no_webhook,
        webhook_port=args.webhook_port,
        tolerance_sec=args.tolerance_sec,
        require_mutation=args.require_mutation,
        timeout_bayar=args.timeout_bayar,
    )
    threading.Thread(target=kasir.preload_ocr, daemon=True, name="preload-ocr").start()

    if args.pay_image:
        img = cv2.imread(args.pay_image)
        if img is None:
            raise SystemExit(f"gambar bukti '{args.pay_image}' tidak terbaca")
        data = kasir._baca_bukti(img)
        print(f"[kasir] bukti: {data['status']} {fmt_rupiah(data['jumlah'] or 0)} | {data['teks'][:80]}")
        if data["jumlah"]:
            kasir.dibayar, kasir.ref = data["jumlah"], data["referensi"]
            print(
                f"[kasir] bukti terbaca {fmt_rupiah(kasir.dibayar)}; "
                "otomatis dipakai setelah minuman"
            )

    source = int(args.source) if str(args.source).isdigit() else args.source
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise SystemExit(f"sumber '{args.source}' tidak bisa dibuka")
    if isinstance(source, int):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.cam_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.cam_height)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    disp = None
    if args.fullscreen and not args.no_window:
        disp = screen_size()
        cv2.namedWindow("Kasir Kulkas", cv2.WINDOW_NORMAL)
        cv2.setWindowProperty("Kasir Kulkas", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        print(f"[kasir] fullscreen {disp[0]}x{disp[1]}")

    writer = None
    if args.save:
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        out_size = disp if disp else (
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280,
            int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720,
        )
        writer = cv2.VideoWriter(
            args.save, cv2.VideoWriter_fourcc(*"mp4v"),
            cap.get(cv2.CAP_PROP_FPS) or 25, out_size,
        )

    print("[kasir] jalan. r=reset spasi=paksa s=simpan q=keluar")
    n = 0
    last_det, last_total = [], 0
    t_prev = time.perf_counter()
    t_loop = t_prev
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        now = time.perf_counter()
        if now > t_prev:
            inst = 1.0 / (now - t_prev)
            kasir.fps = inst if kasir.fps == 0 else 0.85 * kasir.fps + 0.15 * inst
        t_prev = now
        deteksi, total = kasir.step(frame)
        if deteksi:
            last_det, last_total = deteksi, total
        tampil = kasir._gambar(frame, deteksi, total)
        if disp:
            tampil = letterbox(tampil, disp)
        if writer is not None:
            writer.write(tampil)
        if not args.no_window:
            cv2.imshow("Kasir Kulkas", tampil)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("r"):
            kasir.reset("Direset manual")
        elif key == ord(" ") and kasir.state == "BAYAR" and kasir.last_amount:
            kasir.dibayar = kasir.last_amount
            print(f"[kasir] paksa terima {fmt_rupiah(kasir.dibayar)}")
            kasir._evaluasi(frame, kasir.hitung, kasir.frame_minuman)
        elif key == ord("s"):
            daftar = ", ".join(f"{d[1]}" for d in last_det) or "-"
            kasir._catat(daftar, last_total, "MANUAL", "")
            kasir.pesan, kasir.warna = "Transaksi disimpan", (0, 255, 255)
            print(f"[kasir] simpan manual total={fmt_rupiah(last_total)}")
        n += 1
        if args.max_frames and n >= args.max_frames:
            break

    cap.release()
    if writer is not None:
        writer.release()
    if not args.no_window:
        cv2.destroyAllWindows()
    dur = time.perf_counter() - t_loop
    fps = n / dur if dur > 0 else 0
    print(f"[kasir] selesai. {n} frame dalam {dur:.1f}s ({fps:.1f} fps loop), log: {args.log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
