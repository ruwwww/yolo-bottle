# Panduan: Capture (lokal) → Labeling (GPU) → Training

Capture dan labeling **dipisah total**:

```
LAPTOP (webcam, tanpa model)                SERVER GPU (RTX 5060 Ti)
┌──────────────────────────┐               ┌──────────────────────────────┐
│ 1. capture per kelas     │  tar.gz/scp   │ 2. Moondream detect per kelas│
│    dataset/capture/...   │ ────────────► │    prompt: "bottle",         │
│    (frame mentah)        │               │    "pen box", "computer mouse"│
│                          │ ◄──────────── │    → YOLO labels (.txt)      │
└──────────────────────────┘  images+labels└──────────────────────────────┘
            │
            ▼
 3. split → 4. train (train_remote.py, GPU) → 5. weights/*.pt
```

Tidak ada deteksi saat capture: frame tersimpan apa adanya, kamera tetap lancar,
dan laptop bisa offline. Label baru dibuat saat langkah 2 di server.

## Kelas & prompt

| Tombol | `classes.txt`    | Prompt awal (bisa diubah)        | Alternatif prompt              |
|--------|------------------|----------------------------------|--------------------------------|
| 1      | `bottle`         | `bottle`                         | `water bottle`, `plastic bottle` |
| 2      | `ballpoint_box`  | `ballpoint box`                  | `pen box`, `box of pens`       |
| 3      | `computer_mouse` | `computer mouse`                 | `mouse`, `wireless mouse`      |
| 4      | `teh_pucuk`      | `teh pucuk bottle`               | `teh pucuk`, `iced tea bottle` |
| 5      | `golda`          | `golda coffee bottle`            | `golda coffee`, `coffee bottle` |

Urutan di `dataset/capture/classes.txt` = **class-id YOLO** (0, 1, 2, 3, 4).
Jangan diubah setelah ada label. Prompt ada di `dataset/capture/prompts.txt`
dan boleh diedit kapan saja sebelum labeling.

## Struktur folder

```
dataset/capture/
  classes.txt            # bottle / ballpoint_box / computer_mouse / teh_pucuk / golda
  prompts.txt            # bottle=bottle, teh_pucuk=teh pucuk bottle, ...
  manifest.csv           # file, kelas, waktu, source, frame
  bottle/frame_*.jpg
  ballpoint_box/frame_*.jpg
  computer_mouse/frame_*.jpg
  teh_pucuk/frame_*.jpg
  golda/frame_*.jpg

dataset/raw/             # hasil labeling (YOLO)
  classes.txt
  images/*.jpg
  labels/*.txt           # kosong = negatif
  labeling.csv           # file, kelas, boxes, prompt, ms
```

## 1. Capture (laptop + webcam)

```powershell
.venv\Scripts\python.exe src\collect_dataset.py --classes bottle,ballpoint_box,computer_mouse,teh_pucuk,golda
# atau pintasan:
.\scripts\6_capture_kelas.ps1
```

Tombol saat jendela capture aktif:

- `1`..`5` → simpan frame **saat itu** ke `capture/<kelas>/`
  (`4` = `teh_pucuk`, `5` = `golda`).
- `spasi` → simpan ulang dengan kelas terakhir.
- `a` → auto-save on/off (mis. tiap 1 detik).
- `q` / `ESC` → keluar.

Tanpa kamera (uji dari video):

```powershell
.venv\Scripts\python.exe src\collect_dataset.py --source build\ets_all.mp4 `
  --class bottle --auto-save 0.5 --max 20 --no-window
```

Tips pengambilan data:

- 50–150 foto per kelas; variasi jarak, sudut, cahaya, dan latar.
- Pastikan objek terlihat jelas dan tidak terpotong; hindari blur.
- Ambil juga 10–20 frame **tanpa objek** (tekan tombol kelas saat objek tidak ada
  → nanti jadi label kosong/negatif, bagus untuk menekan false positive).
- Nama file otomatis unik (timestamp), jadi aman menekan tombol berkali-kali.

## 2. Labeling di server GPU (Moondream)

```powershell
# semua gambar (upload → label → unduh label)
.venv\Scripts\python.exe src\label_remote.py

# uji cepat dulu 3 gambar per kelas
.venv\Scripts\python.exe src\label_remote.py --limit 3

# prompt khusus
.venv\Scripts\python.exe src\label_remote.py --prompts "ballpoint_box=pen box,computer_mouse=wireless mouse"

# pintasan (langsung split setelah unduh label)
.\scripts\9_label_moondream.ps1 -Limit 3
```

Yang terjadi:

1. Folder `dataset/capture` dipack, di-upload ke `~/yolo-bottle/label_job/`.
2. `label_moondream.py` jalan di server (conda env `ai`) memakai GPU; model
   `moondream2` dimuat sekali (~35 s), lalu ±40 ms/gambar.
3. Label YOLO (`cls cx cy w h`, koordinat 0..1) ditulis, gambar tanpa objek
   disimpan dengan label kosong (negatif) — kecuali `--skip-empty`.
4. Hasil diunduh ke `dataset/raw/images|labels` + `dataset/raw/labeling.csv`.

Cek hasil cepat:

```powershell
Get-Content dataset\raw\labeling.csv | Select-Object -First 8
Get-Content dataset\raw\labels\<salah-satu>.txt
```

Prompt jelek → 0 kotak. Uji prompt lebih dulu dengan `--limit 2`, lihat kolom
`boxes` di `labeling.csv`; kalau 0, ganti prompt di `dataset/capture/prompts.txt`
atau lewat `--prompts`. Untuk objek kecil, naikkan `--resize 960`.

Server GPU diatur dengan:

```powershell
.\scripts\8_moondream_server.ps1          # cek/nyalakan service Moondream HTTP (opsional)
.\scripts\8_moondream_server.ps1 -Stop
```

> `label_remote.py` **tidak butuh** service HTTP; ia menjalankan model langsung
> di server (lebih cepat, tanpa overhead jaringan per gambar). Karena VRAM hanya
> 16 GB, script otomatis mematikan service HTTP sementara lalu menyalakannya
> lagi setelah selesai (`--keep-server` / `--no-restart` untuk mengubahnya).

## 3. Split + training

```powershell
.venv\Scripts\python.exe src\split_dataset.py --keep-empty
.venv\Scripts\python.exe src\train_remote.py --dataset dataset `
  --names bottle,ballpoint_box,computer_mouse,teh_pucuk,golda `
  --model yolo11s.pt --epochs 100 --out weights\multiclass_best.pt
```

Pintasan: `.\scripts\7_train_kelas.ps1`.

## 4. Pakai model

```powershell
.venv\Scripts\python.exe src\kasir_kulkas.py --detector yolo --model weights\multiclass_best.pt
```

Untuk kasir: tambahkan baris kelas + harga di `menu.csv` (nama kelas harus sama
dengan `classes.txt`), lalu:

```powershell
.venv\Scripts\python.exe src\kasir_kulkas.py --detector yolo --model weights\multiclass_best.pt
```

## Menambah data di kemudian hari

1. Jalankan capture lagi (folder lama tetap, file baru menumpuk).
2. Jalankan `python src/label_remote.py` — hanya memproses yang ada di folder
   capture; hasilnya digabung ke `dataset/raw` (file dengan nama sama ditimpa).
3. Split + training ulang seperti biasa.

Kalau ingin melabeli ulang dari nol, hapus `dataset/raw` dulu.

## Troubleshooting

- **`classes.txt tidak ada`** → belum capture; jalankan langkah 1.
- **SSH gagal** → cek `ssh 100.91.215.116 whoami`; key harus tanpa password.
- **Semua `boxes=0` untuk satu kelas** → prompt tidak cocok. Coba sinonim
  (`pen box`, `box of pens`; `mouse`), atau naikkan `--resize 960`.
- **Label kosong tapi file ada** → itu negatif (memang disengaja).
- **Labeling lambat** → normalnya ±40 ms/gambar + upload; gambar besar diperkecil
  ke `--resize 640`. Untuk 300 gambar: ±2–4 menit termasuk load model.
- **GPU sibuk** → jangan jalankan training bersamaan dengan labeling.
- **Koneksi putus saat unduh** → hasil labeling tetap di server
  (`~/yolo-bottle/label_job/raw`). Jangan label ulang; cukup:
  `.\scripts\9_label_moondream.ps1 -DownloadOnly` (atau
  `python src/label_remote.py --download-only`) setelah server online.
- **Kelas tertukar** → urutan `classes.txt` menentukan class-id; jangan diubah
  setelah labeling. Jika berubah, hapus `dataset/raw` dan label ulang.
