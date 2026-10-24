# yolo-bottle

Pipeline deteksi botol: auto-labeling dari webcam → training di server GPU via SSH →
demo weight hasil training + demo OCR nominal pembayaran (`Rp X.XXX`) dari layar HP.

```
src/autolabel.py     webcam + detector COCO pretrained (bottle) -> gambar + label YOLO (maks 50)
src/split_dataset.py split raw -> dataset/images|labels/{train,val} + data.local.yaml
src/train_remote.py  pack dataset -> scp -> train di GPU -> tarik best.pt
src/fetch_weights.py tarik ulang weight dari remote (best.pt/last.pt)
src/demo_yolo.py     demo deteksi (webcam / gambar / video)
src/ocr_payment.py   OCR "Rp X.XXX" dari layar HP lewat webcam (PaddleOCR)
remote/train.sh      script yang dieksekusi di server remote (conda env "ai")
```

## Prasyarat

- Windows + Python 3.12, webcam
- `uv` (dipakai untuk membuat venv lokal)
- SSH key yang sudah bisa login ke `100.91.215.116` (user `kuroko`)
- Remote: Ubuntu + NVIDIA RTX 5060 Ti, conda env `ai` (Python 3.11, torch cu130, ultralytics)

## Setup sekali

```powershell
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r requirements-ocr.txt
```

Tanpa OCR (lebih ringan): pakai `requirements.txt` saja.

Konfigurasi remote (opsional, ada default):

```powershell
Copy-Item .env.example .env
# YOLO_SSH_HOST=100.91.215.116
# YOLO_REMOTE_DIR=yolo-bottle
# YOLO_PYTHON=$HOME/.conda/envs/ai/bin/python
```

## 1. Auto-label dari webcam

```powershell
.venv\Scripts\python.exe src\autolabel.py --source 0 --max 50 --conf 0.5
```

- Detector pretrained COCO (`yolo11n.pt`), hanya kelas `bottle` (COCO id 39).
- Setiap frame yang mengandung botol disimpan **maksimal 50 gambar** (jeda `--cooldown`
  1 detik agar tidak duplikat), beserta label YOLO `.txt` dan preview ber-box.
- Output: `dataset/raw/images`, `dataset/raw/labels`, `dataset/raw/preview`,
  `dataset/raw/metadata.csv`.
- Tombol: `q` keluar, `s` simpan manual, `spasi` pause.
- Uji tanpa webcam: `--source video.mp4 --no-window`.

## 2. Split dataset

```powershell
.venv\Scripts\python.exe src\split_dataset.py
```

40/10 (80/20) train/val + membuat `data.local.yaml` untuk validasi lokal.

## 3. Training di GPU remote

```powershell
.venv\Scripts\python.exe src\train_remote.py --epochs 100 --batch 8 --imgsz 640
```

Yang dilakukan:

1. cek SSH + GPU (`nvidia-smi`), cek disk remote
2. **upload dataset hanya kalau berubah** (sha isi dataset dibandingkan dengan remote).
   Kalau sama: upload dilewati, yang dikirim hanya `data.yaml` + `train.sh` (~KB).
   Paksa upload ulang: `--force-upload`. Lewati total: `--skip-setup`.
3. jalankan `remote/train.sh` dengan **conda env `ai`** (tanpa install apa pun).
   Kalau env tidak ada, otomatis fallback: install `uv` → venv → torch cu128.
4. training dengan `nohup` + polling log (tahan disconnect), `MLFLOW_ALLOW_FILE_STORE=true`
5. `scp` `best.pt` → `weights/bottle_best.pt`, plus `results.csv/png` → `runs/bottle/`

Opsi penting: `--host`, `--remote-dir`, `--python`, `--model yolo11s.pt`,
`--epochs`, `--patience` (`0` = tanpa early stop), `--foreground` (stream log).

Ambil weight saja:

```powershell
.venv\Scripts\python.exe src\fetch_weights.py --which best.pt
```

## 4. Demo deteksi

```powershell
# webcam
.venv\Scripts\python.exe src\demo_yolo.py --weights weights\bottle_best.pt --source 0

# gambar / video, simpan hasil
.venv\Scripts\python.exe src\demo_yolo.py --source foto.jpg --no-window --save-dir demo_output
.venv\Scripts\python.exe src\demo_yolo.py --source video.mp4 --no-window --save demo_output\demo.mp4
```

Lokal hanya ada Intel Iris Xe (tanpa CUDA), jadi demo berjalan di CPU (~50 ms/frame
untuk yolo11n @640). GPU hanya dipakai saat training di remote.

## 5. OCR pembayaran dari layar HP

```powershell
.venv\Scripts\python.exe src\ocr_payment.py --source 0
```

- Arahkan layar HP (nominal `Rp ...`) ke kotak ROI di tengah frame.
- Crop ROI → upscale → CLAHE → PaddleOCR; teks digabung lalu di-parse dengan regex
  `Rp/IDR` (mendukung `250.000`, `1.234.567,89`, `Rp25.000,00`, salah baca `O→0`, `l→1`).
- Nominal baru dinyatakan **TERVERIFIKASI** setelah 3 frame sepakat (voting),
  lalu snapshot + baris `ocr_captures/payments.csv` disimpan otomatis.
- Tombol: `q` keluar, `s` snapshot, `b` ganti preprocessing (`clahe→binary→raw`),
  `d` debug box OCR, `r` reset vote.
- Uji cepat tanpa kamera: `--selftest` (10 kasus parser + OCR gambar sintetis `Rp 250.000`).

```powershell
.venv\Scripts\python.exe src\ocr_payment.py --image foto_layar.jpg --no-window --out demo_output
```

## 6. Kasir kulkas realtime (seperti project ETS)

```powershell
.venv\Scripts\python.exe src\kasir_kulkas.py
# atau tanpa webcam (uji pakai video):
.venv\Scripts\python.exe src\kasir_kulkas.py --pay-image build\ocr_selftest.png --source build\ets_all.mp4 --no-window --save demo_output\kasir_demo.mp4
```

Alur 3 tahap seperti mode "kulkas" punya teman:

1. **BAYAR** - tunjukkan bukti bayar (layar HP). OCR jalan tiap 0,4 detik di thread
   terpisah, cari nominal `Rp ...` (tanpa perlu kata `Berhasil`). Belum kebaca?
   Tekan `spasi` untuk paksa terima nominal terakhir.
2. **MINUMAN** - tunjukkan minuman. YOLO hitung per kelas x harga `menu.csv`;
   objek harus ter-track >= 0,35 detik DAN stabil 10 frame (mayoritas) supaya
   false positive tidak ikut terhitung.
3. **HASIL** - total <= dibayar -> "Terima kasih"; lebih -> layar merah + foto
   disimpan di `pelanggaran/` + bunyi. Semua tercatat di `transaksi.csv`.

Default model: `weights/ets_v3.pt` (OpenVINO, 1 kelas `air_mineral`) + `menu.csv`
dari `Downloads\ETS`; tanpa folder itu pakai `weights/bottle_best.pt` dengan harga
default (`--harga-default 5000`). Kontrol: `r` reset, `s` simpan manual, `q` keluar.

Catatan: dataset ETS yang tersedia hanya berisi label `air_mineral`, jadi model
9 kelas lama pun sebenarnya cuma pernah memprediksi `air_mineral`. Model baru
dibuat 1 kelas dengan negatif keras supaya tidak asal mendeteksi.

### Optimasi untuk laptop ini (Intel Iris Xe, tanpa CUDA)

| Bagian | Sebelum | Sesudah |
|---|---|---|
| YOLO (yolo11s, 640) | torch CPU 148 ms (6.8 FPS) | OpenVINO **iGPU ~16-20 ms (50+ FPS)** |
| Loop kamera | **1.9 FPS** | **45-58 FPS** |
| OCR bukti bayar | PaddleOCR 3.x **47 s** (mkldnn rusak) | Windows OCR **~80 ms** |
| Waktu deteksi ke-1 | full kompilasi GPU ~18 s | cache GPU `build/ov_cache` |

### Akurasi: false positive ditekan

Model lama (9 kelas) tidak punya contoh latar kosong, jadi asal kotak di gambar
apa pun (222 false positive di 122 foto COCO tanpa botol). `weights/ets_v3.pt`
dilatih ulang (1 kelas `air_mineral`, 103 foto + 176 negatif keras: COCO128 tanpa
botol, crop latar, dll) sehingga:

| Uji | model lama | `ets_v3` |
|---|---|---|
| COCO128 tanpa botol (holdout 32 foto) | 49 FP di 22 foto | **0 FP** |
| Picsum acak (20 foto) | 13 FP di 7 foto | **0 FP** |
| Foto ETS test (10 foto, 32 botol) | 15 terdeteksi @0.5 | **21 terdeteksi @0.45** |

Ditambah filter di aplikasi: `--min-area 0.01` (buang kotak <1% frame),
`--min-confirm 3` + `--confirm-sec 0.35` (objek harus bertahan & ter-track
minimal 0,35 detik sebelum dihitung), dan kotak yang belum terkonfirmasi
digambar abu-abu tipis saja. Uji video negatif 100 frame: 0 transaksi palsu.

Cara kerjanya:

- `src/accelerate.py` mengekspor model ke OpenVINO **sekali** (`*_openvino_model/`)
  lalu menjalankan di iGPU (`device='intel:GPU'`), dengan cache kernel di `build/ov_cache`.
- OCR memakai Windows OCR (`winocr`, lang `en`) yang sudah ada di Windows; PaddleOCR
  hanya fallback (`--ocr paddle`). Nominal `Rp 250.000` tetap terbaca sempurna.
- `--det-every 2` untuk menghemat CPU (deteksi tiap 2 frame, kotak dipakai ulang),
  `--imgsz 512` kalau masih ingin lebih ringan, `--device cpu` untuk paksa CPU.
- Kalau di ruangan asli masih ada false positive: kumpulkan 20-50 fotonya
  (tanpa minuman), taruh sebagai gambar + label kosong di `dataset3/images/train`
  & `dataset3/labels/train`, lalu latih ulang:
  `python src/train_remote.py --dataset dataset3 --class-name air_mineral --model yolo11s.pt --epochs 60 --name v5`
- Thread dibatasi (`torch.set_num_threads(6)`, OpenCV 1 thread) supaya kamera,
  deteksi, dan OCR tidak saling berebut.

Tampilan layar penuh:

```powershell
.venv\Scripts\python.exe src\kasir_kulkas.py --fullscreen
.\scripts\5_kasir.ps1 -Fullscreen
```

## 7. Dataset kelas lain: capture -> labeling (GPU) -> training

Capture dan labeling **terpisah**: capture di laptop tanpa model, labeling
Moondream di server GPU, training di GPU. Panduan lengkap + contoh prompt:
**`docs/CAPTURE_LABELING.md`**.

```powershell
# 1) capture webcam: tombol 1..9 = simpan frame ke kelas itu (tanpa label)
.venv\Scripts\python.exe src\collect_dataset.py --classes bottle,ballpoint_box,computer_mouse
.\scripts\6_capture_kelas.ps1

# 2) upload -> label Moondream di GPU -> unduh label
.venv\Scripts\python.exe src\label_remote.py --limit 3   # uji cepat
.\scripts\9_label_moondream.ps1

# 3) split + training
.venv\Scripts\python.exe src\split_dataset.py --keep-empty
.venv\Scripts\python.exe src\train_remote.py --dataset dataset --names bottle,ballpoint_box,computer_mouse --out weights\multiclass_best.pt

# 4) demo kasir multiclass (menu + harga di menu_multiclass.csv)
.\scripts\10_kasir_multiclass.ps1 -Source build\demo_mixed.mp4 -PayImage build\pay_500k.png -Save build\kasir_multiclass.mp4
```

`menu_multiclass.csv` berisi harga bottle/ballpoint_box/computer_mouse. Bukti
bayar contoh: `build/pay_500k.png` (OCR winocr membaca `Rp 500.000`). Demo siap
pakai: `build/kasir_bottle.mp4`, `build/kasir_ballpoint_box.mp4`,
`build/kasir_computer_mouse.mp4` (masing-masing `OK` + tercatat di
`transaksi.csv`).

Prompt per kelas ada di `dataset/capture/prompts.txt` (mis. `bottle=bottle`,
`ballpoint_box=pen box`, `computer_mouse=computer mouse`); edit lalu jalankan
`label_remote.py` lagi. Label kosong = negatif (bagus untuk menekan false
positive). Output: `dataset/raw/images|labels` + `dataset/raw/labeling.csv`.

## 8. Backend Moondream (text-guided bbox, GPU server)

Moondream terpasang di server `100.91.215.116` (RTX 5060 Ti) pada conda env `ai`;
torch tidak diubah (`2.13.0+cu130`), hanya paket `moondream` + `kestrel*` yang
ditambah. Ada dua pemakaian:

- **Labeling dataset** (`src/label_remote.py`): model dimuat langsung di server,
  tanpa HTTP per gambar (paling cepat).
- **Deteksi realtime** (`src/kasir_kulkas.py --detector moondream`): lewat HTTP
  `POST /detect`, prompt teks -> bbox.

```powershell
.\scripts\8_moondream_server.ps1            # start + tunggu siap (~35 s)
.\scripts\8_moondream_server.ps1 -Stop      # matikan
.\scripts\8_moondream_server.ps1 -Log       # lihat log
```

Catatan: labeling langsung butuh VRAM penuh, jadi `label_remote.py` otomatis
mematikan server HTTP sementara lalu menyalakannya lagi (bisa dilewati dengan
`--keep-server`; server tidak dinyalakan lagi dengan `--no-restart`).

Endpoint: `GET /health`, `POST /detect` (`remote/moondream_server.py`).
Prompt yang bagus: `bottle`, `water bottle`, `pen box`, `computer mouse`.
`--md-label` memetakan semua kotak ke satu kelas menu (default `air_mineral`).

## Troubleshooting

- **Paddle error `OneDnnContext ... fused_conv2d` / `ConvertPirAttribute...`**
  bug oneDNN PaddlePaddle 3.x di Windows. Script sudah memakai `enable_mkldnn=False`.
- **`import paddleocr` gagal `torch/lib/shm.dll`**: urutan import; script meng-import
  `torch` sebelum `paddleocr` (konflik DLL MKL di Windows).
- **MLflow `filesystem tracking backend ... maintenance mode`**: set
  `MLFLOW_ALLOW_FILE_STORE=true` (sudah di `remote/train.sh`).
- **`python3 -m venv` gagal `ensurepip`** di remote: pakai conda env `ai`
  (default) atau biarkan script memasang `uv`.
- **GPU tidak terpakai**: cek baris log `CUDA:0 (NVIDIA GeForce RTX 5060 Ti)` dan
  `device: '0'`. Kalau `torch.cuda.is_available()` False, berarti yang dipakai
  torch CPU (bukan GPU).

## Hasil uji (smoke test)

- Auto-label 50 gambar dari rekaman gambar ETS → split 40 train / 10 val
- Training remote: `mAP50 ≈ 0.90`, `mAP50-95 ≈ 0.75`, 32 epoch (± 4 menit di RTX 5060 Ti)
- Weight final: `weights/bottle_best.pt` (5.2 MB) — `last.pt` epoch terakhir, karena
  `best.pt` dari early-stop epoch 2 confidence-nya belum matang (deteksi < 0.02).
- Demo: 20/20 gambar uji terdeteksi (confidence maks 0.985)
- Artefak demo: `demo_output/deteksi_*.jpg`, `demo_output/demo_bottle.mp4`,
  `demo_output/ocr_ocr_selftest.jpg`

### Multiclass (bottle / ballpoint_box / computer_mouse)

- Capture 477 frame (bottle 146, ballpoint_box 188, computer_mouse 143), label
  Moondream di GPU, split 446 train / 112 val.
- Training `yolo11s` 100 epoch (RTX 5060 Ti, ±34 menit): mAP50 `0.748`,
  mAP50-95 `0.656`; val per kelas: bottle mAP50 `0.896`, computer_mouse `0.958`,
  ballpoint_box `0.404`.
- Cek deteksi di val (conf 0.35): bottle P/R `1.00/1.00`, computer_mouse
  `0.91/1.00`, ballpoint_box `0.83/0.28` → kotak pensil masih lemah (banyak frame
  dilabeli negatif oleh Moondream; perbaiki prompt `pen box` / `box of pens` lalu
  label ulang).
- Demo: `build/demo_mixed.mp4` (18 gambar val, 3 kelas) → `build/demo_multiclass.mp4`;
  weight `weights/multiclass_best.pt` (19 MB).
