"""Script pembuat slide PowerPoint (PPTX) untuk presentasi Kasir Kulkas Kejujuran.
Menghasilkan file presentation_kasir_kulkas.pptx dengan desain modern 16:9 widescreen.
"""

from pathlib import Path
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PPTX = ROOT / "presentation_kasir_kulkas.pptx"

# Palette Warna Modern Dark UI
C_BG = RGBColor(15, 23, 42)          # Slate 900
C_CARD = RGBColor(30, 41, 59)        # Slate 800
C_CARD_BORDER = RGBColor(51, 65, 85) # Slate 700
C_TEXT_MAIN = RGBColor(248, 250, 252)# White / Slate 50
C_TEXT_MUTED = RGBColor(148, 163, 184)# Slate 400
C_CYAN = RGBColor(56, 189, 248)      # Cyan 400
C_EMERALD = RGBColor(52, 211, 153)   # Emerald 400
C_AMBER = RGBColor(251, 191, 36)     # Amber 400
C_ROSE = RGBColor(244, 63, 94)       # Rose 500
C_INDIGO = RGBColor(129, 140, 248)   # Indigo 400

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
blank_layout = prs.slide_layouts[6]


def add_bg(slide):
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
    bg.fill.solid()
    bg.fill.fore_color.rgb = C_BG
    bg.line.fill.background()
    return bg


def add_header(slide, category: str, title: str):
    # Category badge
    cat_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.5), Inches(0.35))
    tf_cat = cat_box.text_frame
    tf_cat.word_wrap = True
    p_cat = tf_cat.paragraphs[0]
    p_cat.text = category.upper()
    p_cat.font.size = Pt(10)
    p_cat.font.bold = True
    p_cat.font.color.rgb = C_CYAN

    # Title
    title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.7), Inches(11.5), Inches(0.7))
    tf_title = title_box.text_frame
    tf_title.word_wrap = True
    p_title = tf_title.paragraphs[0]
    p_title.text = title
    p_title.font.size = Pt(22)
    p_title.font.bold = True
    p_title.font.color.rgb = C_TEXT_MAIN


def add_card(slide, left, top, width, height, fill_color=C_CARD, border_color=C_CARD_BORDER):
    card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    card.fill.solid()
    card.fill.fore_color.rgb = fill_color
    card.line.color.rgb = border_color
    card.line.width = Pt(1.2)
    return card


# ==========================================================
# SLIDE 1: COVER
# ==========================================================
slide1 = prs.slides.add_slide(blank_layout)
add_bg(slide1)

# Glow badge
badge = add_card(slide1, Inches(0.8), Inches(1.3), Inches(4.5), Inches(0.45), fill_color=RGBColor(24, 38, 70), border_color=C_CYAN)
tf = badge.text_frame
p = tf.paragraphs[0]
p.text = "SISTEM EMBEDDED & COMPUTER VISION"
p.alignment = PP_ALIGN.CENTER
p.font.size = Pt(11)
p.font.bold = True
p.font.color.rgb = C_CYAN

# Main Title
tbox = slide1.shapes.add_textbox(Inches(0.8), Inches(1.9), Inches(11.5), Inches(2.2))
tf = tbox.text_frame
tf.word_wrap = True
p1 = tf.paragraphs[0]
p1.text = "Kasir Kulkas Kejujuran Berbasis AI"
p1.font.size = Pt(36)
p1.font.bold = True
p1.font.color.rgb = C_TEXT_MAIN

p2 = tf.add_paragraph()
p2.text = "Deteksi YOLOv11, OCR Struk Transfer, Integrasi QRIS Midtrans, & Rekonsiliasi Mutasi Anti-Kecurangan"
p2.font.size = Pt(18)
p2.font.color.rgb = C_CYAN

# Subtitle card
sub_card = add_card(slide1, Inches(0.8), Inches(4.3), Inches(11.7), Inches(2.2))
tf = sub_card.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Deskripsi & Keunggulan Utama Proyek:"
p.font.size = Pt(13)
p.font.bold = True
p.font.color.rgb = C_TEXT_MAIN

bullets = [
    "Otomasi Kasir Mandiri: Menghitung total belanja botol minuman secara instan & akurat tanpa barcode scanner manual.",
    "Triangulasi 3 Titik Waktu: Mengeliminasi 99% celah kecurangan transfer statis (Replay Attack, Fake Screenshot, & Double Spending).",
    "Dual & Triple Capture Forensik: Menyimpan foto minuman, scan struk, dan foto wajah pelanggar saat terjadi kecurangan.",
    "Admin Monitoring Real-Time: Webapp Dashboard live sync + Cloudflare Tunnel + notifikasi alert Telegram Bot instan.",
]
for b in bullets:
    pb = tf.add_paragraph()
    pb.text = "• " + b
    pb.font.size = Pt(11)
    pb.font.color.rgb = C_TEXT_MUTED


# ==========================================================
# SLIDE 2: LATAR BELAKANG & PERMASALAHAN
# ==========================================================
slide2 = prs.slides.add_slide(blank_layout)
add_bg(slide2)
add_header(slide2, "Latar Belakang & Masalah", "Tantangan Kulkas Kejujuran & 4 Celah Kecurangan Pembeli")

cards_data = [
    ("1. Replay Attack (Screenshot Lama)", "Pembeli menunjukkan screenshot struk transfer kemarin atau tadi pagi yang sudah kadaluarsa ke kamera kasir.", C_ROSE),
    ("2. Fake Screenshot (Struk Editan)", "Pembeli mengedit nominal, status, dan jam pada template struk aplikasi transfer tanpa ada uang yang benar-benar masuk rekening.", C_AMBER),
    ("3. Double Spending (1 Struk 2 Botol)", "Dua orang pembeli bergantian menggunakan satu bukti transfer yang sama untuk mengambil dua botol minuman berbeda.", C_ROSE),
    ("4. Underpayment (Kurang Bayar)", "Total tagihan minuman Rp 15.000, tetapi pembeli hanya mentransfer Rp 3.000 dan langsung mengambil barang.", C_AMBER),
]

for i, (title, desc, accent) in enumerate(cards_data):
    row = i // 2
    col = i % 2
    left = Inches(0.8 + col * 5.9)
    top = Inches(1.8 + row * 2.5)
    c = add_card(slide2, left, top, Inches(5.6), Inches(2.2))
    tf = c.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title
    p.font.size = Pt(14)
    p.font.bold = True
    p.font.color.rgb = accent
    p_desc = tf.add_paragraph()
    p_desc.text = desc
    p_desc.font.size = Pt(11)
    p_desc.font.color.rgb = C_TEXT_MUTED


# ==========================================================
# SLIDE 3: ARSITEKTUR SISTEM
# ==========================================================
slide3 = prs.slides.add_slide(blank_layout)
add_bg(slide3)
add_header(slide3, "Arsitektur Sistem", "Integrasi Hardware, Edge Computer Vision, & Cloud Gateway")

arch_blocks = [
    ("1. INPUT & SENSOR", "• Webcam Standar 1080p/720p\n• Mendeteksi botol di panggung kasir\n• Membaca layar struk HP pembeli", Inches(0.8)),
    ("2. EDGE COMPUTING", "• YOLOv11 Multi-class Model\n• WinOCR / PaddleOCR Realtime\n• State Machine Kasir (Python)\n• OpenCV Display + QR Code", Inches(3.8)),
    ("3. WEBHOOK & TUNNEL", "• HTTP Webhook Server (Port 8091)\n• Cloudflare Named Tunnel\n  (kulkas.ruwwww-thesis.my.id)\n• Verifikasi SHA-512 Signature", Inches(6.8)),
    ("4. CLOUD & MONITORING", "• Midtrans Payment Gateway Snap\n• Telegram Bot Push Alert (@tekber_bot)\n• Admin Monitoring Dashboard (Tailwind)\n• SQLite WAL Persistence (kulkas.db)", Inches(9.8)),
]

for title, desc, left in arch_blocks:
    c = add_card(slide3, left, Inches(1.8), Inches(2.7), Inches(4.8))
    tf = c.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title
    p.font.size = Pt(12)
    p.font.bold = True
    p.font.color.rgb = C_CYAN
    p_desc = tf.add_paragraph()
    p_desc.text = desc
    p_desc.font.size = Pt(10)
    p_desc.font.color.rgb = C_TEXT_MUTED


# ==========================================================
# SLIDE 4: USER FLOW & STATE MACHINE
# ==========================================================
slide4 = prs.slides.add_slide(blank_layout)
add_bg(slide4)
add_header(slide4, "Alur Transaksi Kasir", "State Machine: MINUMAN -> BAYAR -> HASIL")

states = [
    ("Tahap 1: MINUMAN", "Pembeli meletakkan minuman di depan kasir. Model YOLO mendeteksi jenis botol, menguji kestabilan 10 frame berturut-turut, lalu mengunci total tagihan belanja.", C_EMERALD, Inches(0.8)),
    ("Tahap 2: BAYAR & COUNTDOWN", "Layar kasir menampilkan QR Code Midtrans dinamis dan progress bar hitung mundur expirasi 60 detik. Pembeli scan bayar & menghadapkan struk ke kamera.", C_CYAN, Inches(4.8)),
    ("Tahap 3: HASIL & EVALUASI", "Kasir memvalidasi nominal, jam struk, dan mutasi uang masuk. Jika valid -> Layar Hijau. Jika curang/kurang bayar/waktu habis -> Layar Merah Alarm.", C_ROSE, Inches(8.8)),
]

for title, desc, color, left in states:
    c = add_card(slide4, left, Inches(1.8), Inches(3.7), Inches(4.8))
    tf = c.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title
    p.font.size = Pt(14)
    p.font.bold = True
    p.font.color.rgb = color
    p_desc = tf.add_paragraph()
    p_desc.text = desc
    p_desc.font.size = Pt(11)
    p_desc.font.color.rgb = C_TEXT_MUTED


# ==========================================================
# SLIDE 5: METODOLOGI TRIANGULASI 3 TITIK WAKTU
# ==========================================================
slide5 = prs.slides.add_slide(blank_layout)
add_bg(slide5)
add_header(slide5, "Inovasi Rekonsiliasi", "Formula Triangulasi 3 Titik Waktu Anti-Kecurangan")

rules = [
    ("Lapisan 1: Validasi Nominal", "Nominal OCR Struk >= Total Tagihan Kasir", "Memastikan pembeli membayar sesuai/lebih dari harga barang. Jika kurang -> REJECT UNDERPAYMENT.", C_CYAN, Inches(1.8)),
    ("Lapisan 2: Validasi Jam Struk vs Kamera", "| T_struk - T_kamera | <= 180 detik", "Memastikan struk dibuat maksimal 3 menit yang lalu dari waktu hadap kamera. Mencegah Replay Attack.", C_EMERALD, Inches(3.4)),
    ("Lapisan 3: Validasi Mutasi Bank Masuk", "| T_mutasi - T_struk | <= 180 detik", "Memastikan dana benar-benar masuk ke rekening bank/Midtrans dalam rentang 3 menit yang sama. Mencegah Struk Palsu.", C_AMBER, Inches(5.0)),
]

for title, formula, desc, color, top in rules:
    c = add_card(slide5, Inches(0.8), top, Inches(11.7), Inches(1.35))
    tf = c.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title + "  ➔  " + formula
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = color
    p_desc = tf.add_paragraph()
    p_desc.text = desc
    p_desc.font.size = Pt(10.5)
    p_desc.font.color.rgb = C_TEXT_MUTED


# ==========================================================
# SLIDE 6: KLASIFIKASI STATUS TRANSAKSI
# ==========================================================
slide6 = prs.slides.add_slide(blank_layout)
add_bg(slide6)
add_header(slide6, "Logika Verifikasi", "3 Tingkatan Status Pembayaran yang Transparan")

statuses = [
    ("1. LUNAS (MUTASI FIX)", "OK_REKONSILIASI_MUTASI", "Nominal struk pas/lebih DAN mutasi dana di Midtrans/Bank sudah terkonfirmasi masuk dan berhasil diklaim (claimed=True). Tidak ada celah double spend.", C_EMERALD, Inches(0.8)),
    ("2. PENDING (STRUK VALID)", "PENDING_MUTASI", "Struk berhasil dibaca OCR dan jam struk valid, namun mutasi dana di rekening bank belum terkonfirmasi masuk. Kasir memberikan status pending audit.", C_AMBER, Inches(4.8)),
    ("3. PELANGGARAN (FRAUD)", "PELANGGARAN: ...", "Terjadi kecurangan: kurang bayar (underpayment), jam struk kadaluarsa (>3 menit), mutasi rekening nihil, atau waktu bayar habis (>60 detik expired).", C_ROSE, Inches(8.8)),
]

for title, code, desc, color, left in statuses:
    c = add_card(slide6, left, Inches(1.8), Inches(3.7), Inches(4.8))
    tf = c.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = color
    p_code = tf.add_paragraph()
    p_code.text = f"Kode: {code}"
    p_code.font.size = Pt(9.5)
    p_code.font.color.rgb = C_CYAN
    p_desc = tf.add_paragraph()
    p_desc.text = desc
    p_desc.font.size = Pt(10.5)
    p_desc.font.color.rgb = C_TEXT_MUTED


# ==========================================================
# SLIDE 7: SISTEM TRIPLE CAPTURE (BUKTI FOTO FORENSIK)
# ==========================================================
slide7 = prs.slides.add_slide(blank_layout)
add_bg(slide7)
add_header(slide7, "Forensik Digital", "Sistem Penyimpanan 3 Foto Otentik Per Kasus")

photos = [
    ("FOTO 1: SCAN MINUMAN", "Format: YYYYMMDD_HHMMSS_minuman.jpg\n\nTangkapan kamera saat pembeli meletakkan botol minuman di panggung kasir dan deteksi bounding box YOLO telah terkunci stabil.", C_EMERALD, Inches(0.8)),
    ("FOTO 2: SCAN BUKTI TF", "Format: YYYYMMDD_HHMMSS_struk.jpg\n\nTangkapan kamera saat pembeli menghadapkan layar HP berisi struk pembayaran ke webcam kasir untuk dibaca oleh modul OCR.", C_CYAN, Inches(4.8)),
    ("FOTO 3: FOTO PELANGGARAN", "Format: YYYYMMDD_HHMMSS_pelanggaran.jpg\n\nTangkapan kamera saat alarm berbunyi: merekam wajah pembeli dan suasana kasir saat terjadi underpayment atau kecurangan.", C_ROSE, Inches(8.8)),
]

for title, desc, color, left in photos:
    c = add_card(slide7, left, Inches(1.8), Inches(3.7), Inches(4.8))
    tf = c.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = color
    p_desc = tf.add_paragraph()
    p_desc.text = desc
    p_desc.font.size = Pt(10.5)
    p_desc.font.color.rgb = C_TEXT_MUTED


# ==========================================================
# SLIDE 8: TABEL HASIL PENGUJIAN 6 SKENARIO
# ==========================================================
slide8 = prs.slides.add_slide(blank_layout)
add_bg(slide8)
add_header(slide8, "Hasil & Pengujian", "Hasil Uji Eksperimental 6 Skenario Transaksi")

table_shape = slide8.shapes.add_table(7, 4, Inches(0.8), Inches(1.8), Inches(11.7), Inches(4.8))
table = table_shape.table
table.columns[0].width = Inches(0.6)
table.columns[1].width = Inches(3.2)
table.columns[2].width = Inches(4.5)
table.columns[3].width = Inches(3.4)

headers = ["No", "Skenario Pengujian", "Aksi Pembeli / Penguji", "Status Akhir Sistem"]
for j, h in enumerate(headers):
    cell = table.cell(0, j)
    cell.fill.solid()
    cell.fill.fore_color.rgb = RGBColor(24, 38, 70)
    p = cell.text_frame.paragraphs[0]
    p.text = h
    p.font.size = Pt(11)
    p.font.bold = True
    p.font.color.rgb = C_CYAN

rows_data = [
    ("1", "Happy Path (Transaksi Normal)", "Beli Rp 3.000, bayar Midtrans Rp 3.000, struk pas jam valid.", "LUNAS (MUTASI FIX)"),
    ("2", "Underpayment (Kurang Bayar)", "Beli barang Rp 50.000, struk transfer hanya Rp 3.000.", "PELANGGARAN: KURANG BAYAR"),
    ("3", "Replay Attack (Screenshot Lama)", "Tunjukkan struk kemarin (>3 menit lalu) tanpa transfer baru.", "PELANGGARAN: KADALUARSA"),
    ("4", "Mutasi Nihil (Struk Palsu)", "Tunjukkan struk editan jam sekarang tanpa transfer riil ke rekening.", "PELANGGARAN: MUTASI NIHIL"),
    ("5", "Double Spend (1 Struk 2x)", "Menggunakan kembali struk yang sudah berhasil dipakai sebelumnya.", "PELANGGARAN: DOUBLE SPEND"),
    ("6", "Timeout Expired (Waktu Habis)", "Ambil barang, masuk tahap bayar, lalu diam/kabur > 60 detik.", "PELANGGARAN: TIMEOUT EXPIRED"),
]

for i, row in enumerate(rows_data):
    for j, val in enumerate(row):
        cell = table.cell(i + 1, j)
        cell.fill.solid()
        cell.fill.fore_color.rgb = C_CARD if i % 2 == 0 else RGBColor(20, 29, 47)
        p = cell.text_frame.paragraphs[0]
        p.text = val
        p.font.size = Pt(9.5)
        if j == 3:
            p.font.bold = True
            p.font.color.rgb = C_EMERALD if "LUNAS" in val else C_ROSE
        else:
            p.font.color.rgb = C_TEXT_MAIN


# ==========================================================
# SLIDE 9: ADMIN WEB DASHBOARD
# ==========================================================
slide9 = prs.slides.add_slide(blank_layout)
add_bg(slide9)
add_header(slide9, "Monitoring & Kontrol", "Admin Web Dashboard Real-Time & Live Telemetry")

dash_features = [
    ("Live KPI Metrics", "Memantau Total Sesi, Jumlah Lunas, Jumlah Pending, Persentase Fraud, dan Total Pendapatan secara langsung.", C_CYAN),
    ("Filter Status 5 Kategori", "Pemisahan filter instan: Semua, Lunas (Mutasi Fix), Pending Mutasi, Pelanggaran, dan Timeout.", C_EMERALD),
    ("Triple-Preview Modal", "Inspeksi 3 foto berdampingan (Foto Minuman, Scan TF, Foto Pelanggaran) dalam satu popup perbandingan.", C_INDIGO),
    ("Feed Mutasi Webhook", "Daftar mutasi masuk real-time dari Midtrans port 8091 beserta ID transaksi dan status klaim dana.", C_AMBER),
]

for i, (title, desc, color) in enumerate(dash_features):
    row = i // 2
    col = i % 2
    left = Inches(0.8 + col * 5.9)
    top = Inches(1.8 + row * 2.5)
    c = add_card(slide9, left, top, Inches(5.6), Inches(2.2))
    tf = c.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title
    p.font.size = Pt(14)
    p.font.bold = True
    p.font.color.rgb = color
    p_desc = tf.add_paragraph()
    p_desc.text = desc
    p_desc.font.size = Pt(11)
    p_desc.font.color.rgb = C_TEXT_MUTED


# ==========================================================
# SLIDE 10: KESIMPULAN & PENUTUP
# ==========================================================
slide10 = prs.slides.add_slide(blank_layout)
add_bg(slide10)
add_header(slide10, "Kesimpulan", "Keunggulan Inovasi & Nilai Tambah Sistem Kasir Kulkas")

conc_card = add_card(slide10, Inches(0.8), Inches(1.8), Inches(11.7), Inches(4.8))
tf = conc_card.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Kesimpulan & Pencapaian Tugas Akhir / Skripsi:"
p.font.size = Pt(16)
p.font.bold = True
p.font.color.rgb = C_CYAN

conclusions = [
    "Efisiensi Biaya Signifikan: Menggantikan sensor berat / RFID berbiaya jutaan rupiah hanya dengan webcam standar dan model computer vision efisien (YOLOv11 OpenVINO).",
    "Ketahanan Anti-Fraud Tinggi: Metode triangulasi 3 titik waktu (|Δt| <= 180s) berhasil menangkal 4 teknik kecurangan utama (Replay Attack, Fake Struk, Underpayment, & Double Spend).",
    "Klasifikasi Status Transparan: Memisahkan secara tegas antara status Lunas (Mutasi Fix), Pending (Struk Valid), dan Pelanggaran (Fraud).",
    "Sistem Audit Terintegrasi: Rekaman forensik 3 foto dan notifikasi push Telegram Bot memungkinkan pemilik memantau kulkas dari mana saja secara real-time.",
    "Siap Demo (Fresh State Architecture): Dilengkapi mekanisme reset titik 0 dan pencadangan otomatis untuk presentasi berulang.",
]

for c_text in conclusions:
    p = tf.add_paragraph()
    p.text = "✔ " + c_text
    p.font.size = Pt(11.5)
    p.font.color.rgb = C_TEXT_MAIN

prs.save(str(OUTPUT_PPTX))
print(f"[PPTX] File presentasi berhasil dibuat: {OUTPUT_PPTX}")
