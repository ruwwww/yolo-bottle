"""Modul Rekonsiliasi Pembayaran QRIS Statis & Deteksi Pelanggaran.

Fungsi:
  1. Ekstraksi jam/tanggal dari hasil OCR struk pembayaran HP.
  2. Validasi selisih waktu (|waktu_scan - waktu_struk| <= toleransi) -> Anti-Screenshot Lama.
  3. Pencocokan mutasi rekening masuk (feed mutasi / webhook) -> Anti-Struk Palsu.
  4. Proteksi Double-Spending: struk / mutasi yang sama tidak dapat diklaim dua kali.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

# Load environment variable jika ada modul common
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import ROOT, load_env

    load_env()
except Exception:
    ROOT = Path(__file__).resolve().parents[1]

TIME_RE = re.compile(
    r"\b([01]?\d|2[0-3])[:.]([0-5]\d)(?:[:.]([0-5]\d))?\s*(?:wib|wita|wit)?\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"\b(\d{1,2})[-/\s]([a-zA-Z]{3,9}|\d{1,2})[-/\s](\d{2,4})\b"
)

ID_MONTHS = {
    "jan": 1, "januari": 1, "january": 1,
    "feb": 2, "februari": 2, "february": 2,
    "mar": 3, "maret": 3, "march": 3,
    "apr": 4, "april": 4,
    "mei": 5, "may": 5,
    "jun": 6, "juni": 6, "june": 6,
    "jul": 7, "juli": 7, "july": 7,
    "agu": 8, "agustus": 8, "aug": 8, "august": 8,
    "sep": 9, "september": 9,
    "okt": 10, "oktober": 10, "oct": 10, "october": 10,
    "nop": 11, "nov": 11, "november": 11,
    "des": 12, "desember": 12, "dec": 12, "december": 12,
}


def parse_receipt_datetime(texts: list[str], default_date: datetime | None = None) -> datetime | None:
    """Ekstraksi tanggal & jam dari kumpulan baris teks OCR struk pembayaran."""
    base = default_date or datetime.now()
    parsed_time: time | None = None
    parsed_date: tuple[int, int, int] | None = None  # (year, month, day)

    for t in texts:
        clean = t.strip()
        if not clean:
            continue

        # Cari jam
        if parsed_time is None:
            m_time = TIME_RE.search(clean)
            if m_time:
                hh = int(m_time.group(1))
                mm = int(m_time.group(2))
                ss = int(m_time.group(3)) if m_time.group(3) else 0
                if 0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59:
                    parsed_time = time(hh, mm, ss)

        # Cari tanggal
        if parsed_date is None:
            m_date = DATE_RE.search(clean)
            if m_date:
                raw_d, raw_m, raw_y = m_date.group(1), m_date.group(2).lower(), m_date.group(3)
                try:
                    d = int(raw_d)
                    y = int(raw_y)
                    if y < 100:
                        y += 2000
                    if raw_m.isdigit():
                        m = int(raw_m)
                    else:
                        m = ID_MONTHS.get(raw_m, 0)
                    if 1 <= d <= 31 and 1 <= m <= 12 and 2020 <= y <= 2040:
                        parsed_date = (y, m, d)
                except Exception:
                    pass

    if parsed_time is None:
        return None

    if parsed_date is not None:
        try:
            return datetime(parsed_date[0], parsed_date[1], parsed_date[2],
                            parsed_time.hour, parsed_time.minute, parsed_time.second)
        except ValueError:
            pass

    # Jika tanggal tidak tertulis eksplisit di layar, gunakan tanggal hari ini
    return datetime(base.year, base.month, base.day,
                    parsed_time.hour, parsed_time.minute, parsed_time.second)


@dataclass
class Mutation:
    id: str
    amount: int
    timestamp: datetime
    source: str = "webhook"  # midtrans, bank, manual
    ref: str = "-"
    claimed: bool = False
    raw: dict | None = None


class MutationStore:
    """Penyimpanan mutasi rekening masuk (in-memory + append log mutasi.jsonl)."""

    def __init__(self, log_path: Path | str | None = None):
        self.log_path = Path(log_path or (ROOT / "mutasi.jsonl"))
        self._lock = threading.Lock()
        self._mutations: dict[str, Mutation] = {}
        self._load_existing()

    def _load_existing(self):
        if not self.log_path.exists():
            return
        try:
            for line in self.log_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                ts = datetime.fromisoformat(d["timestamp"])
                m = Mutation(
                    id=str(d["id"]),
                    amount=int(d["amount"]),
                    timestamp=ts,
                    source=d.get("source", "log"),
                    ref=d.get("ref", "-"),
                    claimed=bool(d.get("claimed", False)),
                    raw=d.get("raw"),
                )
                self._mutations[m.id] = m
        except Exception as exc:
            print(f"[mutasi] Gagal load log mutasi: {exc}")

    def add(self, m_id: str, amount: int, timestamp: datetime | None = None,
            source: str = "webhook", ref: str = "-", raw: dict | None = None) -> Mutation:
        with self._lock:
            ts = timestamp or datetime.now()
            m = Mutation(
                id=str(m_id),
                amount=int(amount),
                timestamp=ts,
                source=source,
                ref=ref,
                claimed=False,
                raw=raw,
            )
            self._mutations[m.id] = m
            self._persist_append(m)
            return m

    def _persist_append(self, m: Mutation):
        try:
            row = {
                "id": m.id,
                "amount": m.amount,
                "timestamp": m.timestamp.isoformat(),
                "source": m.source,
                "ref": m.ref,
                "claimed": m.claimed,
            }
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as exc:
            print(f"[mutasi] Gagal simpan mutasi ke file: {exc}")

    def claim(self, m_id: str) -> bool:
        with self._lock:
            if m_id in self._mutations and not self._mutations[m_id].claimed:
                self._mutations[m_id].claimed = True
                self._persist_all()
                return True
            return False

    def _persist_all(self):
        try:
            lines = []
            for m in self._mutations.values():
                row = {
                    "id": m.id,
                    "amount": m.amount,
                    "timestamp": m.timestamp.isoformat(),
                    "source": m.source,
                    "ref": m.ref,
                    "claimed": m.claimed,
                }
                lines.append(json.dumps(row))
            self.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as exc:
            print(f"[mutasi] Gagal rewrite log mutasi: {exc}")

    def list_recent(self, window_seconds: int = 3600) -> list[Mutation]:
        cutoff = datetime.now() - timedelta(seconds=window_seconds)
        with self._lock:
            return [m for m in self._mutations.values() if m.timestamp >= cutoff]

    def count(self) -> int:
        with self._lock:
            return len(self._mutations)


class Reconciler:
    """Sistem rekonsiliasi pembayaran struk QRIS vs mutasi rekening & deteksi pelanggaran."""

    def __init__(
        self,
        store: MutationStore | None = None,
        tolerance_seconds: int = 180,     # Toleransi selisih waktu 3 menit (180s)
        require_mutation: bool = False,   # Wajib ada mutasi masuk di rekening
        used_refs: set[str] | None = None,
    ):
        self.store = store or MutationStore()
        self.tolerance = tolerance_seconds
        self.require_mutation = require_mutation
        self.used_refs = set(used_refs or [])

    def verify(
        self,
        nominal_ocr: int,
        total_tagihan: int,
        scan_time: datetime,
        receipt_time: datetime | None = None,
        referensi: str = "-",
    ) -> tuple[bool, str, dict]:
        """Evaluasi pembayaran: lolos verifikasi atau pelanggaran.

        Returns:
            (is_ok: bool, kategori_hasil: str, detail: dict)
        """
        detail: dict = {
            "nominal_ocr": nominal_ocr,
            "total_tagihan": total_tagihan,
            "scan_time": scan_time.strftime("%Y-%m-%d %H:%M:%S"),
            "receipt_time": receipt_time.strftime("%Y-%m-%d %H:%M:%S") if receipt_time else None,
            "referensi": referensi,
            "matched_mutation_id": None,
        }

        # 1. Validasi Nominal Dasar
        if nominal_ocr < total_tagihan:
            kurang = total_tagihan - nominal_ocr
            detail["kurang"] = kurang
            return False, "PELANGGARAN_KURANG_BAYAR", detail

        # 2. Anti-Double Spend Ref OCR
        ref_clean = referensi.strip().upper()
        if ref_clean and ref_clean != "-":
            if ref_clean in self.used_refs:
                return False, "PELANGGARAN_DOUBLE_SPEND_REF", detail
            self.used_refs.add(ref_clean)

        # 3. Validasi Timestamp Jam Struk vs Jam Kamera (Anti-Screenshot Lama)
        if receipt_time is not None:
            diff_scan_receipt = abs((scan_time - receipt_time).total_seconds())
            detail["time_diff_scan_receipt_sec"] = int(diff_scan_receipt)
            if diff_scan_receipt > self.tolerance:
                detail["keterangan"] = (
                    f"Struk kadaluarsa: jam struk ({receipt_time.strftime('%H:%M:%S')}) "
                    f"selisih {int(diff_scan_receipt)}s dari jam kamera ({scan_time.strftime('%H:%M:%S')})"
                )
                return False, "PELANGGARAN_TIMESTAMP_KADALUARSA", detail

        # 4. Rekonsiliasi dengan Mutasi Rekening (Feed Mutasi)
        matched_mutation = None
        recent_mutations = self.store.list_recent(window_seconds=self.tolerance * 3)

        for m in recent_mutations:
            if m.claimed:
                continue
            if m.amount < total_tagihan:
                continue

            # Bandingkan waktu mutasi dengan waktu scan & waktu struk
            ref_time = receipt_time if receipt_time is not None else scan_time
            diff_mutation_receipt = abs((m.timestamp - ref_time).total_seconds())
            diff_mutation_scan = abs((m.timestamp - scan_time).total_seconds())

            if diff_mutation_receipt <= self.tolerance and diff_mutation_scan <= self.tolerance:
                matched_mutation = m
                detail["time_diff_mutation_receipt_sec"] = int(diff_mutation_receipt)
                detail["matched_mutation_id"] = m.id
                break

        if matched_mutation is not None:
            # Klaim mutasi agar tidak bisa double spend
            self.store.claim(matched_mutation.id)
            return True, "OK_REKONSILIASI_MUTASI", detail

        # Jika mutasi belum ditemukan di rekening:
        if self.require_mutation:
            detail["keterangan"] = (
                f"Mutasi tidak ditemukan / waktu tidak berdekatan: "
                f"tidak ada dana masuk Rp {total_tagihan:,} dalam rentang ±{self.tolerance}s"
            )
            return False, "PELANGGARAN_MUTASI_NIHIL", detail

        # Jika require_mutation=False: Struk valid secara visual, tapi mutasi bank belum fix
        detail["keterangan"] = "Struk valid secara visual, namun mutasi bank belum terkonfirmasi masuk"
        return True, "PENDING_MUTASI", detail


def verify_midtrans_signature(
    order_id: str,
    status_code: str,
    gross_amount: str,
    server_key: str,
    received_signature: str,
) -> bool:
    """Verifikasi signature webhook notifikasi Midtrans."""
    payload = f"{order_id}{status_code}{gross_amount}{server_key}"
    expected = hashlib.sha512(payload.encode("utf-8")).hexdigest()
    return expected.lower() == received_signature.lower().strip()
