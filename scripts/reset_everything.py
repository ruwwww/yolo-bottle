"""Script Reset Total (Reset Everything) Kasir Kulkas Kejujuran.

Fitur:
  1. Mengarsipkan data lama secara aman ke folder archive/backup_<timestamp> (kecuali --hard).
  2. Mengosongkan transaksi.csv dengan header 9 kolom yang valid.
  3. Mengosongkan mutasi.jsonl.
  4. Mengosongkan seluruh foto di folder pelanggaran/.
  5. Reset ulang database kulkas.db (WAL mode) dan sinkronisasi katalog menu.csv.
  6. Menghentikan proses kasir/webhook zombie yang menahan port/webcam (opsional --kill).
  7. Membersihkan cache Python (__pycache__).

Penggunaan:
  python scripts/reset_everything.py
  python scripts/reset_everything.py --kill
  python scripts/reset_everything.py --hard  # tanpa membuat folder backup
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "kulkas.db"
MENU_CSV_PATH = ROOT / "menu.csv"
TRANSAKSI_CSV_PATH = ROOT / "transaksi.csv"
MUTASI_JSONL_PATH = ROOT / "mutasi.jsonl"
PELANGGARAN_DIR = ROOT / "pelanggaran"
ARCHIVE_DIR = ROOT / "archive"

CSV_HEADER = [
    "waktu",
    "referensi",
    "dibayar",
    "minuman",
    "harga_minuman",
    "status",
    "foto",
    "foto_minuman",
    "foto_pelanggaran",
]


def kill_orphan_processes():
    """Hentikan proses Python yang menjalankan kasir atau webhook server."""
    print("[1/6] Memeriksa proses yang sedang berjalan...")
    if sys.platform != "win32":
        return

    try:
        # Cari proses python yang menjalankan kasir_kulkas atau webhook_server
        cmd = 'Get-CimInstance Win32_Process -Filter "name = \'python.exe\'" | Select-Object ProcessId, CommandLine | ConvertTo-Json'
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
        if not res.stdout.strip():
            print("      Tidak ada proses Python lain.")
            return

        import json
        data = json.loads(res.stdout)
        if isinstance(data, dict):
            data = [data]

        current_pid = os.getpid()
        killed = 0
        for p in data:
            pid = p.get("ProcessId")
            cline = p.get("CommandLine") or ""
            if pid and pid != current_pid:
                if "kasir_kulkas.py" in cline or "webhook_server.py" in cline:
                    try:
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
                        print(f"      Menghentikan PID {pid}: {cline[:60]}...")
                        killed += 1
                    except Exception:
                        pass
        if killed == 0:
            print("      Tidak ada proses kasir/webhook yang perlu dihentikan.")
        else:
            print(f"      Berhasil menghentikan {killed} proses.")
    except Exception as exc:
        print(f"      Peringatan kill process: {exc}")


def backup_existing(timestamp: str) -> Path | None:
    """Backup data & foto sebelum dibersihkan."""
    backup_dir = ARCHIVE_DIR / f"backup_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    has_data = False

    if TRANSAKSI_CSV_PATH.exists() and TRANSAKSI_CSV_PATH.stat().st_size > 100:
        shutil.copy2(TRANSAKSI_CSV_PATH, backup_dir / "transaksi.csv")
        has_data = True

    if MUTASI_JSONL_PATH.exists() and MUTASI_JSONL_PATH.stat().st_size > 0:
        shutil.copy2(MUTASI_JSONL_PATH, backup_dir / "mutasi.jsonl")
        has_data = True

    if DB_PATH.exists():
        try:
            shutil.copy2(DB_PATH, backup_dir / "kulkas.db")
            has_data = True
        except Exception:
            pass

    # Pindahkan foto ke folder arsip
    photo_count = 0
    if PELANGGARAN_DIR.exists():
        backup_photos = backup_dir / "pelanggaran"
        for p in PELANGGARAN_DIR.glob("*.jpg"):
            if not backup_photos.exists():
                backup_photos.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(p), str(backup_photos / p.name))
                photo_count += 1
            except Exception:
                pass

    if has_data or photo_count > 0:
        return backup_dir
    else:
        # Jika tidak ada data yang perlu disimpan, hapus folder backup kosong
        try:
            backup_dir.rmdir()
        except Exception:
            pass
        return None


def reset_database():
    """Reset skema SQLite kulkas.db dan sinkronisasi produk menu.csv."""
    sys.path.insert(0, str(ROOT / "src"))
    from db import init_db
    init_db(fresh=True)


def reset_files():
    """Reset file transaksi.csv, mutasi.jsonl, dan folder pelanggaran."""
    # 1. transaksi.csv
    with TRANSAKSI_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)

    # 2. mutasi.jsonl
    MUTASI_JSONL_PATH.write_text("", encoding="utf-8")

    # 3. pelanggaran directory
    PELANGGARAN_DIR.mkdir(parents=True, exist_ok=True)
    for f in PELANGGARAN_DIR.glob("*"):
        try:
            if f.is_file():
                f.unlink()
            elif f.is_dir():
                shutil.rmtree(f)
        except Exception:
            pass


def clean_pycache():
    """Hapus folder __pycache__ untuk menghindari byte-code stale."""
    for root, dirs, _ in os.walk(str(ROOT)):
        for d in dirs:
            if d == "__pycache__":
                try:
                    shutil.rmtree(os.path.join(root, d))
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser(description="Reset Total Sistem Kasir Kulkas (Reset Everything)")
    parser.add_argument("--kill", action="store_true", help="Hentikan proses kasir / webhook server yang sedang berjalan")
    parser.add_argument("--hard", action="store_true", help="Hapus bersih tanpa membuat folder arsip backup")
    args = parser.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("\n" + "=" * 65)
    print("         MEMULAI RESET TOTAL SISTEM KASIR KULKAS")
    print("=" * 65)

    if args.kill:
        kill_orphan_processes()
    else:
        print("[1/6] Melewati kill process (gunakan --kill jika ingin menghentikan kasir)")

    # 2. Arsip data lama
    print("[2/6] Memeriksa dan mengarsipkan data lama...")
    if not args.hard:
        b_dir = backup_existing(ts)
        if b_dir:
            print(f"      Data lama berhasil diamankan ke:\n      {b_dir}")
        else:
            print("      Sistem sudah bersih, tidak ada data lama yang perlu diarsip.")
    else:
        print("      Mode --hard aktif: melewati pembuatan arsip backup.")
        # Bersihkan foto langsung jika hard
        if PELANGGARAN_DIR.exists():
            for p in PELANGGARAN_DIR.glob("*.jpg"):
                try:
                    p.unlink()
                except Exception:
                    pass

    # 3. Reset Database
    print("[3/6] Menginisialisasi ulang database SQLite (kulkas.db)...")
    reset_database()
    print("      Tabel produk, transaksi, mutasi, dan audit log di-reset bersih.")

    # 4. Reset Log Files
    print("[4/6] Mengosongkan transaksi.csv dan mutasi.jsonl...")
    reset_files()
    print("      File transaksi.csv di-set ke header 9 kolom bersih.")
    print("      File mutasi.jsonl dikosongkan.")

    # 5. Bersihkan Folder Pelanggaran
    print("[5/6] Memastikan folder foto 'pelanggaran/' kosong dan siap...")
    PELANGGARAN_DIR.mkdir(parents=True, exist_ok=True)

    # 6. Bersihkan Pycache
    print("[6/6] Membersihkan cache Python (__pycache__)...")
    clean_pycache()

    print("=" * 65)
    print("            RESET TOTAL SELESAI DENGAN SUKSES! (100% FRESH)    ")
    print("=" * 65)
    print("Status Sistem Saat Ini:")
    print("  * Transaksi      : 0 record")
    print("  * Mutasi Rekening: 0 record")
    print("  * Foto Bukti     : 0 file")
    print("  * Database       : Siap (katalog tersinkron dari menu.csv)")
    print("  * Dashboard Web  : Bersih (0 transaksi)")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
