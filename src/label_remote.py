"""Kirim hasil capture ke server GPU, label dengan Moondream, tarik labelnya.

Capture (lokal, tanpa model) dan labeling (GPU) terpisah total:

    # 1. capture di laptop (frame mentah, tanpa label)
    python src/collect_dataset.py --classes bottle,ballpoint_box,computer_mouse,teh_pucuk,golda
    # 2. upload + label di GPU + unduh label
    python src/label_remote.py
    # 3. split + training
    python src/split_dataset.py --keep-empty
    python src/train_remote.py --dataset dataset --names bottle,ballpoint_box,computer_mouse,teh_pucuk,golda

Contoh lain:
    python src/label_remote.py --limit 5            # uji cepat 5 gambar/kelas
    python src/label_remote.py --prompts "computer_mouse=wireless mouse"
    python src/label_remote.py --source dataset/capture --out dataset/raw
"""

from __future__ import annotations

import argparse
import csv
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from collections import Counter
from pathlib import Path

from common import BUILD, DATASET, ROOT, ensure_dirs, load_env
from train_remote import run, scp, scp_down, ssh, ssh_base

CAPTURE = DATASET / "capture"


def parse_prompts(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in text.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


def read_prompts(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def count_images(source: Path) -> tuple[int, Counter]:
    per: Counter = Counter()
    for d in sorted(source.iterdir()):
        if d.is_dir():
            n = len(list(d.glob("*.jpg"))) + len(list(d.glob("*.png")))
            if n:
                per[d.name] = n
    return sum(per.values()), per


def pack(source: Path) -> Path:
    ensure_dirs(BUILD)
    archive = BUILD / "capture_upload.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("classes.txt", "prompts.txt"):
            f = source / name
            if f.exists():
                tar.add(f, arcname=f"capture/{name}")
        for d in sorted(source.iterdir()):
            if d.is_dir():
                tar.add(d, arcname=f"capture/{d.name}")
    return archive


def hentikan_server(host: str) -> None:
    """Matikan uvicorn moondream agar VRAM cukup untuk labeling langsung."""
    ssh(host, "pkill -f 'uvicorn moondream_server' || true", check=False)
    for _ in range(30):
        try:
            used = int(
                ssh(host, "nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits")
                .strip().splitlines()[0]
            )
        except (RuntimeError, ValueError, IndexError):
            time.sleep(1)
            continue
        if used < 12000:
            print(f"      server dihentikan (VRAM terpakai {used} MiB)")
            return
        time.sleep(1)
    print("      [warn] VRAM masih >12 GB; labeling mungkin OOM")


def unduh_tar(host: str, remote_path: str, lokal: Path, percobaan: int = 4) -> None:
    last: Exception | None = None
    for i in range(1, percobaan + 1):
        try:
            run([
                "scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
                f"{host}:{remote_path}", str(lokal),
            ])
            return
        except RuntimeError as exc:
            last = exc
            print(f"      [retry {i}/{percobaan}] unduh terputus, ulangi...")
            time.sleep(3)
    raise last if last else RuntimeError("unduh gagal")


def jalankan_server(host: str, proj: str, python: str, server_port: int) -> None:
    cmd = (
        f"cd {proj} && (nohup {python} -m uvicorn moondream_server:app "
        f"--host 0.0.0.0 --port {server_port} > $HOME/md_setup/server.log 2>&1 &)"
    )
    ssh(host, f"mkdir -p $HOME/md_setup && {cmd}", check=False)
    url = f"http://{host}:{server_port}/health"
    for _ in range(40):
        time.sleep(2)
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    print(f"      server moondream hidup lagi di :{server_port}")
                    return
        except OSError:
            pass
    print("      [warn] server moondream belum merespon; cek scripts/8_moondream_server.ps1 -Log")


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description="Label capture dengan Moondream di GPU remote")
    ap.add_argument("--host", default=os.environ.get("YOLO_SSH_HOST", "100.91.215.116"))
    ap.add_argument("--remote-dir", default=os.environ.get("YOLO_REMOTE_DIR", "yolo-bottle"))
    ap.add_argument(
        "--python",
        default=os.environ.get("YOLO_PYTHON", "$HOME/.conda/envs/ai/bin/python"),
    )
    ap.add_argument("--source", default=str(CAPTURE), help="folder capture (berisi kelas/)")
    ap.add_argument("--out", default=str(DATASET / "raw"), help="tujuan images/ + labels/")
    ap.add_argument("--prompts", default="", help='override, contoh "bottle=bottle"')
    ap.add_argument("--resize", type=int, default=640)
    ap.add_argument("--min-area", type=float, default=0.004)
    ap.add_argument("--limit", type=int, default=0, help="maks gambar per kelas (uji cepat)")
    ap.add_argument("--skip-empty", action="store_true", help="buang gambar tanpa objek")
    ap.add_argument("--server-port", type=int, default=8090)
    ap.add_argument("--keep-server", action="store_true", help="jangan matikan server Moondream")
    ap.add_argument("--no-restart", action="store_true", help="jangan nyalakan server lagi setelah label")
    ap.add_argument("--keep-remote", action="store_true")
    ap.add_argument(
        "--download-only",
        action="store_true",
        help="lompati capture/upload/label; unduh + merge hasil di remote",
    )
    args = ap.parse_args()

    source = Path(args.source)
    out = Path(args.out)
    if not (source / "classes.txt").exists():
        raise SystemExit(f"{source}/classes.txt tidak ada — capture dulu dengan src/collect_dataset.py")
    classes = [c.strip() for c in (source / "classes.txt").read_text(encoding="utf-8").splitlines() if c.strip()]
    total, per = count_images(source)
    if not total:
        raise SystemExit(f"tidak ada gambar di {source}/<kelas>/")
    print("[1/6] capture: " + ", ".join(f"{k}={v}" for k, v in per.items()) + f" (total {total})")

    prompts = {c: c.replace("_", " ") for c in classes}
    prompts.update(read_prompts(source / "prompts.txt"))
    prompts.update(parse_prompts(args.prompts))
    prompt_arg = ",".join(f"{c}={prompts[c]}" for c in classes)
    print("[1/6] prompt : " + ", ".join(f"{c}='{prompts[c]}'" for c in classes))

    print(f"[2/6] cek koneksi {args.host}")
    who = ssh(args.host, "whoami; hostname; echo $HOME").splitlines()
    remote_home = who[-1].strip()
    proj = f"{remote_home}/{args.remote_dir}"
    job = f"{proj}/label_job"

    if args.download_only:
        ada = ssh(args.host, f"test -f {job}/raw/metadata.csv && echo OK || echo MISSING").strip()
        if ada != "OK":
            raise SystemExit(f"tidak ada hasil labeling di {job}/raw")
        print(f"      pakai hasil remote: {job}/raw")
    else:
        ssh(args.host, f"mkdir -p {proj} && rm -rf {job} && mkdir -p {job}")
        print(f"      remote job: {job}")

        print("[3/6] pack + upload capture")
        archive = pack(source)
        scp(args.host, archive, f"{job}/upload.tar.gz")
        ssh(args.host, f"cd {job} && tar -xzf upload.tar.gz && rm -f upload.tar.gz")
        scp(args.host, ROOT / "remote" / "label_moondream.py", f"{job}/label_moondream.py")
        scp(args.host, ROOT / "remote" / "moondream_server.py", f"{proj}/moondream_server.py")
        print(f"      {archive.stat().st_size / 1e6:.1f} MB terkirim")

        extra = "--skip-empty" if args.skip_empty else ""
        limit = f"--limit {args.limit}" if args.limit else ""
        if not args.keep_server:
            print("[4/6] hentikan server Moondream sementara (bebaskan VRAM)")
            hentikan_server(args.host)
        remote_cmd = (
            f"cd {job} && PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True "
            f"{args.python} label_moondream.py "
            f"--root capture --out raw --resize {args.resize} --min-area {args.min_area} "
            f"--prompts {shlex.quote(prompt_arg)} {limit} {extra}"
        )
        print("[4/6] labeling di GPU (Moondream), progres:")
        started = time.time()
        code = subprocess.run(ssh_base(args.host) + [remote_cmd], text=True).returncode
        if not args.keep_server and not args.no_restart:
            print("[4/6] nyalakan lagi server Moondream")
            jalankan_server(args.host, proj, args.python, args.server_port)
        if code != 0:
            raise SystemExit(f"labeling gagal (exit {code})")
        print(f"      selesai dalam {time.time() - started:.1f}s")
        if ssh(args.host, f"test -f {job}/raw/metadata.csv && echo OK || echo MISSING").strip() != "OK":
            raise SystemExit("output labeling tidak ditemukan di remote")

    print("[5/6] unduh label")
    ssh(args.host, f"cd {job} && tar -czf raw.tar.gz raw")
    lokal = BUILD / "raw_remote"
    if lokal.exists():
        shutil.rmtree(lokal)
    ensure_dirs(lokal.parent)
    arsip = BUILD / "raw_download.tar.gz"
    if arsip.exists():
        arsip.unlink()
    unduh_tar(args.host, f"{job}/raw.tar.gz", arsip)
    with tarfile.open(arsip, "r:gz") as tar:
        try:
            tar.extractall(lokal.parent, filter="data")
        except TypeError:
            tar.extractall(lokal.parent)
    arsip.unlink(missing_ok=True)
    ensure_dirs(out / "images", out / "labels")
    n_img = n_lbl = 0
    for f in (lokal / "images").glob("*"):
        shutil.copy2(f, out / "images" / f.name)
        n_img += 1
    for f in (lokal / "labels").glob("*"):
        shutil.copy2(f, out / "labels" / f.name)
        n_lbl += 1
    (out / "classes.txt").write_text(
        "\n".join(classes) + "\n", encoding="utf-8"
    )

    meta_out = out / "labeling.csv"
    baru = not meta_out.exists()
    with meta_out.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if baru:
            w.writerow(["file", "kelas", "boxes", "prompt", "ms"])
        rows = list(csv.DictReader((lokal / "metadata.csv").open(encoding="utf-8")))
        for r in rows:
            w.writerow([r["file"], r["kelas"], r["boxes"], r["prompt"], r["ms"]])

    if not args.keep_remote:
        ssh(args.host, f"rm -rf {job}", check=False)
    shutil.rmtree(lokal, ignore_errors=True)

    per_box: Counter = Counter()
    berisi = 0
    for r in rows:
        if int(r.get("boxes") or 0) > 0:
            berisi += 1
            per_box[r["kelas"]] += 1
    print(
        f"[6/6] selesai: {n_img} gambar, {n_lbl} label, {berisi} berisi kotak "
        f"({', '.join(f'{k}={v}' for k, v in per_box.items()) or '-'})"
    )
    print(f"      output: {out}")
    print(
        "      berikutnya:\n"
        "        python src/split_dataset.py --keep-empty\n"
        f"        python src/train_remote.py --dataset dataset --names {','.join(classes)} "
        "--out weights/multiclass_best.pt"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
