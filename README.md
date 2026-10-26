# yolo-bottle

Kasir kulkas kejujuran realtime: deteksi barang dulu (YOLO/OpenVINO), lalu
verifikasi bukti bayar (OCR nominal `Rp ...`), lalu putuskan OK atau pelanggaran
(pelanggaran bisa dikirim otomatis ke Telegram bot).

Pipeline dataset dipisah tiga tahap: **capture di laptop** (tanpa model) →
**labeling di server GPU pakai Moondream** (VLM) → **training di GPU**.

**Artefak:**

- Kode (repo ini): https://github.com/ruwwww/yolo-bottle
- Dataset 3 kelas (446 train / 112 val + `data.yaml`): https://github.com/ruwwww/yolo-bottle-dataset
- Weight `multiclass_best.pt` (YOLO11s, mAP50 0.748): https://github.com/ruwwww/yolo-bottle-model

## Struktur

```
src/collect_dataset.py   capture frame webcam per kelas (tanpa label, 30 fps)
src/label_remote.py      upload capture -> label Moondream di GPU -> unduh label
src/split_dataset.py     dataset/raw -> dataset/images|labels/{train,val} + data.yaml
src/train_remote.py      pack + upload dataset -> train di GPU -> tarik best.pt
src/kasir_kulkas.py      aplikasi kasir realtime (3 tahap: MINUMAN, BAYAR, HASIL)
src/reconcile.py         rekonsiliasi mutasi rekening & deteksi pelanggaran timestamp
src/webhook_server.py    server HTTP webhook lokal penangkap notifikasi Midtrans/Bank
src/midtrans_pay.py      klien Midtrans Snap API, status polling, & generator QR
src/telegram_notify.py   kirim notifikasi pelanggaran ke Telegram bot (stdlib)
src/accelerate.py        OpenVINO iGPU (auto-export + cache) & tuning thread
src/ocr_payment.py       parser nominal Rp + engine OCR (winocr / PaddleOCR)
src/md_remote.py         klien HTTP server Moondream (deteksi realtime)
remote/label_moondream.py  labeling dataset di GPU (dipanggil label_remote.py)
remote/moondream_server.py server HTTP /detect + /health
remote/train.sh          script training di server (conda env "ai")
menu.csv                 daftar kelas + nama + harga
```

## Prasyarat

- Windows + Python 3.12, webcam
- `uv` untuk membuat venv lokal
- SSH key tanpa password ke server GPU (`~/.ssh/config` host alias, mis. `kuroko`)
- Remote: Ubuntu + NVIDIA GPU, conda env `ai` (torch cu13x + ultralytics + moondream)

## Setup sekali

```powershell
.\scripts\setup.ps1
# atau manual:
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r requirements-ocr.txt
```

Tanpa PaddleOCR (lebih ringan): pakai `requirements.txt` saja.

Konfigurasi remote opsional (default: host `100.91.215.116`, dir `yolo-bottle`):

```powershell
Copy-Item .env.example .env
# YOLO_SSH_HOST=kuroko
# YOLO_REMOTE_DIR=yolo-bottle
# YOLO_PYTHON=$HOME/.conda/envs/ai/bin/python
# MOONDREAM_URL=http://<host>:8090
# TELEGRAM_BOT_TOKEN=123456:ABC...
# TELEGRAM_CHAT_ID=123456789
```

## 1. Capture dataset

```powershell
.\scripts\6_capture_kelas.ps1
# sama dengan:
.venv\Scripts\python.exe src\collect_dataset.py --classes bottle,ballpoint_box,computer_mouse,teh_pucuk,golda
```

- Tombol `1..9` menyimpan frame saat itu ke kelas tersebut, `spasi` simpan kelas
  aktif, `a` auto-save, `q` keluar. Tidak ada deteksi/label di tahap ini.
- Output: `dataset/capture/<kelas>/frame_*.jpg`, plus `classes.txt`, `prompts.txt`,
  `manifest.csv`.
- Prompt labeling bisa diedit di `dataset/capture/prompts.txt` (mis.
  `ballpoint_box=pen box`), atau lewat `--prompts "ballpoint_box=box of pens"`.

## 2. Labeling di GPU (Moondream)

```powershell
.\scripts\9_label_moondream.ps1
# uji cepat 3 gambar/kelas:
.venv\Scripts\python.exe src\label_remote.py --limit 3
```

Alur: pack capture → scp → matikan server Moondream sementara (bebaskan VRAM) →
labeling langsung di GPU → restart server → unduh hasil → merge ke `dataset/raw`.

- Output: `dataset/raw/images`, `dataset/raw/labels`, `dataset/raw/classes.txt`,
  `dataset/raw/labeling.csv` (file, kelas, jumlah box, prompt, ms).
- Label kosong = negatif (bagus untuk menekan false positive).
- Koneksi putus saat unduh? Hasil tetap ada di remote:
  `.venv\Scripts\python.exe src\label_remote.py --download-only`.
- Opsi lain: `--keep-server` (jangan matikan server), `--no-restart`,
  `--skip-empty` (buang gambar tanpa objek), `--keep-remote` (simpan job remote).

## 3. Split + training di GPU

```powershell
.\scripts\7_train_kelas.ps1
# sama dengan:
.venv\Scripts\python.exe src\split_dataset.py --keep-empty
.venv\Scripts\python.exe src\train_remote.py --dataset dataset --model yolo11s.pt `
  --epochs 100 --batch 16 --out weights\multiclass_best.pt
```

`train_remote.py` akan:

1. cek SSH + GPU (`nvidia-smi`) + disk remote
2. upload dataset **hanya kalau berubah** (sha isi dataset); paksa dengan
   `--force-upload`, lewati dengan `--skip-setup`
3. jalankan `remote/train.sh` di conda env `ai` (fallback: venv + torch cu128)
4. training `nohup` + polling log (tahan disconnect)
5. tarik `best.pt` → `weights/multiclass_best.pt` + `results.csv/png` → `runs/multiclass/`

Opsi: `--host`, `--names`, `--epochs`, `--patience` (`0` = tanpa early stop),
`--imgsz`, `--batch`, `--foreground` (stream log).

## 4. Kasir realtime

```powershell
.\scripts\5_kasir.ps1
.\scripts\5_kasir.ps1 -Fullscreen
# atau langsung:
.venv\Scripts\python.exe src\kasir_kulkas.py --detector yolo --model weights\multiclass_best.pt
```

Alur 3 tahap:

1. **MINUMAN** — tunjukkan barang dulu. YOLO menghitung per kelas × harga
   `menu.csv`; objek harus ter-track ≥ 0,35 s **dan** stabil 10 frame (mayoritas)
   supaya false positive tidak ikut dihitung. Total dikunci setelah stabil.
2. **BAYAR** — tunjukkan bukti bayar (layar HP). OCR jalan tiap ~0,4 s di thread
   terpisah dan mencari nominal `Rp ...` (tanpa perlu kata "Berhasil"). Belum
   kebaca? Tekan `spasi` untuk paksa terima nominal terakhir.
3. **HASIL** — total ≤ dibayar → "Terima kasih"; lebih → layar merah + foto
   disimpan di `pelanggaran/` + bunyi + notifikasi Telegram. Bukti bayar yang
   tidak ditunjukkan sampai batas waktu juga dihitung pelanggaran. Semua
   tercatat di `transaksi.csv`.

Isi `menu.csv` (nama kelas harus sama dengan `classes.txt`):

```csv
kelas,nama,harga
bottle,Air Mineral,3000
ballpoint_box,Kotak Pulpen,15000
computer_mouse,Mouse Komputer,50000
teh_pucuk,Teh Pucuk,4000
golda,Golda,4500
```

### Menambah kelas minuman (`teh_pucuk` & `golda`)

Kedua kelas sudah terdaftar di `menu.csv`, default capture, dan prompt labeling.
Weight `multiclass_best.pt` yang ada masih 3 kelas, jadi perlu capture + retrain
agar keduanya benar-benar terdeteksi:

```powershell
.\scripts\6_capture_kelas.ps1     # tombol 4 = teh_pucuk, 5 = golda; 50-150 frame/produk
.\scripts\9_label_moondream.ps1 -Classes teh_pucuk,golda   # hanya kelas baru
.\scripts\7_train_kelas.ps1
```

Harga bisa diubah kapan saja di `menu.csv`.

Uji tanpa webcam:

```powershell
.\scripts\10_kasir_multiclass.ps1 -Source build\demo_mixed.mp4 -PayImage build\pay_500k.png -Save build\kasir.mp4
.venv\Scripts\python.exe src\kasir_kulkas.py --selftest
```

Kontrol: `r` reset, `s` simpan manual, `q` keluar. Opsi penting: `--conf`,
`--detector auto|yolo|moondream`, `--device auto|intel:gpu|intel:cpu|cpu|cuda`,
`--imgsz`, `--det-every`, `--ocr auto|winocr|paddle`, `--min-area`,
`--min-confirm`, `--confirm-sec`, `--fullscreen`, `--save`.

### Notifikasi pelanggaran ke Telegram

Token dan chat tujuan disimpan di `.env` (jangan commit file ini):
```env
TELEGRAM_BOT_TOKEN=<token-dari-BotFather>
TELEGRAM_CHAT_ID=<chat-id>
```

Saat pelanggaran kasir terjadi, foto minuman + bukti bayar otomatis dikirim ke chat tersebut beserta detail total, nominal dibayar, kekurangan, dan referensi transaksi.

**Cek status & tes pengiriman:**
```powershell
.\scripts\12_telegram_bot.ps1 -Info
.\scripts\12_telegram_bot.ps1 -Test
# atau langsung:
.venv\Scripts\python.exe src\kasir_kulkas.py --telegram-test
```

Jika ingin menghubungkan ke chat/akun lain secara otomatis:
```powershell
.\scripts\12_telegram_bot.ps1 -Listen
```
Kirim pesan `/start` ke bot `@tekber_bot` di Telegram, script akan otomatis mendeteksi Chat ID dan menyimpannya ke `.env`.

Matikan notifikasi saat menjalankan kasir dengan `--no-telegram`; atau override lewat `--telegram-token` / `--telegram-chat`.

### Pembayaran Midtrans, QRIS Statis, & Rekonsiliasi Mutasi

Kasir kulkas terintegrasi dengan **Midtrans Payment Gateway** dan **Sistem Rekonsiliasi Mutasi**:
- Di tahap `BAYAR`, kasir menampilkan **QR Code Midtrans** di layar laptop kasir.
- Pembeli scan QR lewat HP > bayar di Midtrans Sandbox (GoPay / ShopeePay / VA).
- Notifikasi pembayaran masuk secara instan ke server webhook lokal kasir (`http://localhost:8091/webhook/midtrans`) via Cloudflare Tunnel (`https://kulkas.ruwwww-thesis.my.id`).
- Pembeli menghadapkan layar HP ("Pembayaran Berhasil") ke kamera kasir.
- **Sistem Rekonsiliasi 3-Arah (`src/reconcile.py`)** memvalidasi:
  1. **Nominal**: Uang masuk >= total belanjaan.
  2. **Anti-Screenshot Lama**: Selisih jam struk HP vs jam kamera laptop kasir $\le 3$ menit (`PELANGGARAN_TIMESTAMP_KADALUARSA`).
  3. **Anti-Struk Palsu**: Memastikan uang benar-benar masuk di mutasi rekening (`PELANGGARAN_MUTASI_NIHIL`).
  4. **Anti-Double Spend**: Struk atau ID mutasi yang sama tidak dapat dipakai dua kali.

> 📖 **Dokumentasi Lengkap:** Lihat [docs/REKONSILIASI_DAN_MIDTRANS.md](docs/REKONSILIASI_DAN_MIDTRANS.md) untuk panduan teknis mendalam dan skenario pengujian demo.

### Dashboard Admin & Live Monitoring

Sistem dilengkapi dashboard web realtime untuk memantau sesi transaksi, status lunas / fraud, dan inspeksi foto capture:
- **Akses Lokal**: [http://localhost:8091/admin](http://localhost:8091/admin)
- **Akses Publik (Cloudflare)**: [https://kulkas.ruwwww-thesis.my.id/admin](https://kulkas.ruwwww-thesis.my.id/admin)

**Fitur Dashboard:**
- Live KPI: Total Sesi, Lunas Terverifikasi, Persentase Fraud, dan Total Pendapatan.
- Filter transaksi (*Semua*, *Lunas*, *Pelanggaran*, *Timeout*).
- Pop-up modal foto capture untuk memeriksa foto pembeli dan bukti bayar.
- Feed mutasi dana masuk real-time dari Midtrans / Bank.
- Auto-refresh otomatis setiap 2 detik.

**Uji coba cepat:**
```powershell
# 1. Cek info koneksi Midtrans
.\scripts\13_midtrans.ps1 -Info

# 2. Simulasi injeksi mutasi masuk Rp 10.000 ke webhook
.\scripts\14_webhook_tunnel.ps1 -Simulate 10000 -Ref "TRX-DEMO-001"

# 3. Jalankan kasir kulkas (mode ketat mutasi)
.venv\Scripts\python.exe src\kasir_kulkas.py --require-mutation --tolerance-sec 180
```

## Backend Moondream (opsional, deteksi realtime)

Moondream juga bisa dipakai sebagai detektor realtime lewat HTTP (prompt teks →
bbox), tanpa training:

```powershell
.\scripts\8_moondream_server.ps1          # start + tunggu siap (~35 s)
.\scripts\8_moondream_server.ps1 -Stop    # matikan
.\scripts\8_moondream_server.ps1 -Log     # lihat log
.venv\Scripts\python.exe src\kasir_kulkas.py --detector moondream --md-prompt "bottle"
```

Endpoint: `GET /health`, `POST /detect`. `--md-label` memetakan semua kotak ke
satu kelas menu (default `bottle`).

## Optimasi untuk laptop ini (Intel Iris Xe, tanpa CUDA)

| Bagian              | Sebelum                               | Sesudah                               |
| ------------------- | ------------------------------------- | ------------------------------------- |
| YOLO (yolo11s, 640) | torch CPU 148 ms (6.8 FPS)            | OpenVINO **iGPU ~16-20 ms (50+ FPS)** |
| Loop kamera         | **1.9 FPS**                           | **45-58 FPS**                         |
| OCR bukti bayar     | PaddleOCR 3.x **47 s** (mkldnn rusak) | Windows OCR **~80 ms**                |
| Waktu deteksi ke-1  | full kompilasi GPU ~18 s              | cache GPU `build/ov_cache`            |

- `src/accelerate.py` mengekspor model ke OpenVINO **sekali** (`*_openvino_model/`)
  lalu jalan di iGPU (`device='intel:GPU'`), cache kernel di `build/ov_cache`.
- OCR memakai Windows OCR (`winocr`, lang `en`); PaddleOCR hanya fallback.
- `--det-every 2` menghemat CPU, `--imgsz 512` lebih ringan lagi, `--device cpu`
  untuk paksa CPU.
- Thread dibatasi (`torch.set_num_threads(6)`, OpenCV 1 thread).

## Troubleshooting

- **`classes.txt tidak ada`** → belum capture; jalankan langkah 1.
- **SSH gagal / timeout** → cek `ssh <host> whoami`; key harus tanpa password.
- **Labeling OOM** → server Moondream masih memegang VRAM; jangan pakai
  `--keep-server`, atau hentikan ComfyUI/service lain.
- **Koneksi putus saat unduh label** → `python src/label_remote.py --download-only`.
- **Paddle error `OneDnnContext ... fused_conv2d`** → bug oneDNN PaddlePaddle 3.x
  di Windows; script sudah `enable_mkldnn=False`.
- **`import paddleocr` gagal `torch/lib/shm.dll`** → urutan import; `ocr_payment.py`
  meng-import `torch` sebelum `paddleocr`.
- **MLflow `filesystem tracking backend ... maintenance mode`** →
  `MLFLOW_ALLOW_FILE_STORE=true` (sudah di `remote/train.sh`).
- **GPU tidak terpakai** → cek log `CUDA:0 (...)` dan `device: '0'`.

## Hasil uji (multiclass)

- Capture 477 frame (bottle 146, ballpoint_box 188, computer_mouse 143), label
  Moondream di GPU, split 446 train / 112 val.
- Training `yolo11s` 100 epoch (RTX 5060 Ti, ±34 menit): mAP50 `0.748`,
  mAP50-95 `0.656`; per kelas: bottle `0.896`, computer_mouse `0.958`,
  ballpoint_box `0.404`.
- Val conf 0.35: bottle P/R `1.00/1.00`, computer_mouse `0.91/1.00`,
  ballpoint_box `0.83/0.28` → kotak pensil masih lemah (banyak frame dilabeli
  negatif; perbaiki prompt `pen box` lalu label ulang).
- Demo kasir 3 kelas: `transaksi.csv` bersih (3.000 / 15.000 / 50.000), semua `OK`.
