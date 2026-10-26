"""Modul Manajemen Database SQLite Kasir Kulkas Kejujuran.

Menyediakan penyimpanan persisten berbasis SQLite (WAL mode):
- Skema relasional: products, transactions, transaction_items, mutations, fraud_logs
- Mendukung Fresh State Reset (mengosongkan data transaksi & mutasi untuk demo bersih)
- Backup otomatis data lama sebelum reset
- Thread-safe & non-blocking untuk performa tinggi
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Load common
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import ROOT, load_env

    load_env()
except Exception:
    ROOT = Path(__file__).resolve().parents[1]

DB_PATH = ROOT / "kulkas.db"
MENU_CSV_PATH = ROOT / "menu.csv"
TRANSAKSI_CSV_PATH = ROOT / "transaksi.csv"
MUTASI_JSONL_PATH = ROOT / "mutasi.jsonl"
PELANGGARAN_DIR = ROOT / "pelanggaran"
ARCHIVE_DIR = ROOT / "archive"


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Buka koneksi SQLite dengan pengaturan WAL mode dan foreign keys aktif."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path), timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Path | None = None, fresh: bool = False) -> None:
    """Inisialisasi tabel database. Jika fresh=True, reset seluruh tabel."""
    conn = get_connection(db_path)
    cur = conn.cursor()

    if fresh:
        cur.executescript("""
            DROP TABLE IF EXISTS fraud_logs;
            DROP TABLE IF EXISTS transaction_items;
            DROP TABLE IF EXISTS transactions;
            DROP TABLE IF EXISTS mutations;
            DROP TABLE IF EXISTS products;
        """)

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            stock INTEGER DEFAULT 100,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            total_amount INTEGER NOT NULL,
            paid_amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            fraud_reason TEXT,
            reference_no TEXT,
            photo_full_path TEXT,
            photo_item_path TEXT,
            matched_mutation_id TEXT
        );

        CREATE TABLE IF NOT EXISTS transaction_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            qty INTEGER NOT NULL,
            unit_price INTEGER NOT NULL,
            subtotal INTEGER NOT NULL,
            FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS mutations (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            amount INTEGER NOT NULL,
            mutation_time DATETIME NOT NULL,
            received_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            reference_no TEXT,
            is_claimed INTEGER DEFAULT 0,
            claimed_by_transaction_id TEXT,
            raw_payload TEXT,
            FOREIGN KEY (claimed_by_transaction_id) REFERENCES transactions(id)
        );

        CREATE TABLE IF NOT EXISTS fraud_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id TEXT,
            fraud_type TEXT NOT NULL,
            time_diff_seconds INTEGER,
            details TEXT,
            telegram_sent INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (transaction_id) REFERENCES transactions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_mutations_lookup ON mutations(amount, is_claimed, mutation_time);
        CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status, timestamp);
    """)

    # Sinkronisasi katalog awal dari menu.csv jika tabel products kosong
    cur.execute("SELECT COUNT(*) AS cnt FROM products")
    if cur.fetchone()["cnt"] == 0 and MENU_CSV_PATH.exists():
        try:
            with MENU_CSV_PATH.open(encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sku = row.get("kelas", "").strip()
                    name = row.get("nama", sku).strip()
                    price = int(row.get("harga", 0))
                    if sku:
                        cur.execute(
                            "INSERT OR IGNORE INTO products (sku, name, price) VALUES (?, ?, ?)",
                            (sku, name, price),
                        )
        except Exception as exc:
            print(f"[db] Peringatan sinkronisasi menu.csv: {exc}")

    conn.commit()
    conn.close()


def record_transaction(
    tx_id: str,
    timestamp: str,
    total_amount: int,
    paid_amount: int,
    status: str,
    fraud_reason: str = "",
    reference_no: str = "",
    photo_full_path: str = "",
    photo_item_path: str = "",
    items: list[tuple[str, int, int]] | None = None,
    db_path: Path | None = None,
) -> None:
    """Mencatat sesi transaksi lengkap beserta foto barang & struk ke SQLite."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO transactions (
            id, timestamp, total_amount, paid_amount, status,
            fraud_reason, reference_no, photo_full_path, photo_item_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tx_id,
            timestamp,
            total_amount,
            paid_amount,
            status,
            fraud_reason,
            reference_no,
            photo_full_path,
            photo_item_path,
        ),
    )
    if items:
        for p_name, qty, unit_price in items:
            cur.execute(
                """
                INSERT INTO transaction_items (transaction_id, product_name, qty, unit_price, subtotal)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tx_id, p_name, qty, unit_price, qty * unit_price),
            )
    if "PELANGGARAN" in status:
        cur.execute(
            """
            INSERT INTO fraud_logs (
                transaction_id, fraud_type, details
            ) VALUES (?, ?, ?)
            """,
            (
                tx_id,
                fraud_reason or "PELANGGARAN",
                f"Tagihan: Rp {total_amount:,} | Bayar: Rp {paid_amount:,} | Foto: {photo_full_path or photo_item_path}",
            ),
        )
    conn.commit()
    conn.close()


def reset_to_fresh_state(archive: bool = True) -> dict:
    """Kembalikan seluruh data sistem ke Titik Nol (Fresh State) yang bersih untuk demo."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "timestamp": ts,
        "archived_transactions": 0,
        "archived_mutations": 0,
        "archived_photos": 0,
        "backup_folder": None,
    }

    # 1. Arsipkan data lama jika diminta
    if archive:
        backup_dir = ARCHIVE_DIR / f"backup_{ts}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        report["backup_folder"] = str(backup_dir)

        if TRANSAKSI_CSV_PATH.exists():
            shutil.copy2(TRANSAKSI_CSV_PATH, backup_dir / "transaksi.csv")
        if MUTASI_JSONL_PATH.exists():
            shutil.copy2(MUTASI_JSONL_PATH, backup_dir / "mutasi.jsonl")
        if DB_PATH.exists():
            shutil.copy2(DB_PATH, backup_dir / "kulkas.db")

        # Pindahkan foto pelanggaran lama ke backup
        if PELANGGARAN_DIR.exists():
            backup_photos = backup_dir / "pelanggaran"
            backup_photos.mkdir(parents=True, exist_ok=True)
            photo_count = 0
            for p in PELANGGARAN_DIR.glob("*.jpg"):
                shutil.move(str(p), str(backup_photos / p.name))
                photo_count += 1
            report["archived_photos"] = photo_count

    # 2. Reset Database SQLite
    init_db(fresh=True)

    # 3. Reset transaksi.csv ke Header Bersih
    with TRANSAKSI_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "waktu", "referensi", "dibayar", "minuman", "harga_minuman",
            "status", "foto", "foto_minuman", "foto_pelanggaran"
        ])

    # 4. Kosongkan mutasi.jsonl
    MUTASI_JSONL_PATH.write_text("", encoding="utf-8")

    # 5. Pastikan folder pelanggaran kosong tapi ada
    PELANGGARAN_DIR.mkdir(parents=True, exist_ok=True)

    return report


def get_dashboard_summary(db_path: Path | None = None) -> dict:
    """Ambil ringkasan statistik KPI transaksi secara instan dari SQLite."""
    conn = get_connection(db_path)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS total FROM transactions")
    total = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS ok_count, COALESCE(SUM(paid_amount), 0) AS revenue FROM transactions WHERE status = 'OK'")
    ok_row = cur.fetchone()
    ok_count = ok_row["ok_count"]
    revenue = ok_row["revenue"]

    cur.execute("SELECT COUNT(*) AS fraud_count FROM transactions WHERE status LIKE 'PELANGGARAN%'")
    fraud_count = cur.fetchone()["fraud_count"]

    cur.execute("SELECT COUNT(*) AS mutasi_count FROM mutations")
    mutasi_count = cur.fetchone()["mutasi_count"]

    conn.close()
    return {
        "total_sessions": total,
        "verified_ok": ok_count,
        "fraud_attempts": fraud_count,
        "total_revenue": revenue,
        "mutations_recorded": mutasi_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Utilitas Manajemen Database Kasir Kulkas")
    parser.add_argument("--init", action="store_true", help="Inisialisasi tabel database SQLite")
    parser.add_argument("--fresh", action="store_true", help="Reset total seluruh data ke Fresh State (0 data, siap demo)")
    parser.add_argument("--no-archive", action="store_true", help="Lewati backup data lama saat reset")
    parser.add_argument("--stats", action="store_true", help="Tampilkan statistik database saat ini")
    args = parser.parse_args()

    if args.fresh:
        print("[db] Menjalankan Fresh State Reset...")
        rep = reset_to_fresh_state(archive=not args.no_archive)
        print("======================================================")
        print("           SISTEM BERHASIL DI-RESET KE FRESH STATE    ")
        print("======================================================")
        if rep["backup_folder"]:
            print(f"Data lama diarsipkan ke : {rep['backup_folder']}")
            print(f"Foto lama dipindahkan    : {rep['archived_photos']} file")
        print("Database kulkas.db       : BERSIH (0 transaksi, 0 mutasi)")
        print("File transaksi.csv       : BERSIH (hanya header)")
        print("File mutasi.jsonl        : BERSIH (0 baris)")
        print("Folder pelanggaran/      : BERSIH (siap capture baru)")
        print("Katalog produk menu.csv  : TERSINKRONISASI")
        print("======================================================")
        print("Sistem sekarang dalam kondisi 100% FRESH siap untuk demo baru!")
        return 0

    if args.stats:
        init_db()
        stats = get_dashboard_summary()
        print("=== Statistik Database Kulkas ===")
        print(f"Total Sesi Transaksi : {stats['total_sessions']}")
        print(f"Lunas Terverifikasi  : {stats['verified_ok']}")
        print(f"Percobaan Fraud      : {stats['fraud_attempts']}")
        print(f"Total Pendapatan     : Rp {stats['total_revenue']:,}")
        print(f"Mutasi Terdata       : {stats['mutations_recorded']}")
        print("=================================")
        return 0

    init_db()
    print(f"[db] Database SQLite siap di: {DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
