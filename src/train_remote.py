"""Latih model botol di server GPU via SSH, lalu tarik weight-nya kembali.

Alur:
  1. pack dataset lokal (images/labels train+val) -> tar.gz
  2. upload ke remote (scp)
  3. install env remote (venv + torch cu128 + ultralytics) lalu train
  4. polling log (tahan disconnect), lalu scp best.pt -> weights/bottle_best.pt

Contoh:
    python src/train_remote.py
    python src/train_remote.py --epochs 150 --batch 16
    python src/train_remote.py --host kuroko --skip-setup --foreground
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

import yaml

from common import BUILD, DATASET, ROOT, RUNS, WEIGHTS, ensure_dirs, load_env

LOCAL_DATA_YAML = ROOT / "data.local.yaml"

for _stream in (sys.stdout, sys.stderr):
    try:
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 - console lama tidak mendukung
        pass


def ssh_base(host: str) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=30",
        host,
    ]


def scp_base() -> list[str]:
    return ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]


def run(cmd: list[str], *, capture: bool = False, check: bool = True) -> str:
    pretty = " ".join(cmd)
    print(f"  $ {pretty}")
    proc = subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and proc.returncode != 0:
        err = (proc.stderr or "").strip()
        raise RuntimeError(f"gagal: {pretty}\n{err}")
    return (proc.stdout or "") if capture else ""


def ssh(
    host: str,
    command: str,
    *,
    capture: bool = True,
    check: bool = True,
    tries: int = 1,
) -> str:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            return run(ssh_base(host) + [command], capture=capture, check=check)
        except RuntimeError as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep(5)
    raise last  # type: ignore[misc]


def scp(host: str, src: Path | str, dst: str) -> None:
    run(scp_base() + [str(src), f"{host}:{dst}"])


def scp_down(host: str, src: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(scp_base() + [f"{host}:{src}", str(dst)])


def dataset_sha(root: Path = DATASET) -> str:
    """Hash isi dataset lokal (train+val) untuk deteksi perubahan."""
    h = hashlib.sha256()
    for split in ("train", "val"):
        for sub in ("images", "labels"):
            folder = root / sub / split
            if not folder.exists():
                continue
            for f in sorted(folder.glob("*")):
                if f.is_file():
                    h.update(f"{sub}/{split}/{f.name}:{f.stat().st_size}\n".encode())
                    h.update(f.read_bytes())
    return h.hexdigest()[:16]


def build_archive(root: Path = DATASET) -> Path:
    ensure_dirs(BUILD)
    archive = BUILD / "dataset.tar.gz"
    missing = [p for p in (root / "images" / "train", root / "images" / "val") if not p.exists()]
    if missing:
        raise SystemExit(
            "Dataset train/val belum ada. Jalankan: python src/split_dataset.py"
        )
    n_train = len(list((root / "images" / "train").glob("*.jpg")))
    n_val = len(list((root / "images" / "val").glob("*.jpg")))
    if n_train == 0:
        raise SystemExit("dataset/images/train kosong. Jalankan auto-label dulu.")

    with tarfile.open(archive, "w:gz") as tar:
        for split in ("train", "val"):
            for sub in ("images", "labels"):
                folder = root / sub / split
                if folder.exists():
                    tar.add(folder, arcname=f"dataset/{sub}/{split}")
    size_mb = archive.stat().st_size / 1e6
    print(f"[pack] {n_train} train + {n_val} val -> {archive.name} ({size_mb:.1f} MB)")
    return archive


def write_data_yaml(remote_dataset: str, names: list[str] | str = "bottle") -> Path:
    if isinstance(names, str):
        names = [names]
    data = {
        "path": remote_dataset,
        "train": "images/train",
        "val": "images/val",
        "names": {i: n for i, n in enumerate(names)},
    }
    BUILD.mkdir(parents=True, exist_ok=True)
    out = BUILD / "data.yaml"
    out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return out


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description="Train YOLO di remote via SSH lalu fetch weights")
    ap.add_argument("--host", default=os.environ.get("YOLO_SSH_HOST", "100.91.215.116"))
    ap.add_argument("--remote-dir", default=os.environ.get("YOLO_REMOTE_DIR", "yolo-bottle"))
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--patience", type=int, default=30, help="0 = matikan early stopping")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", default="8")
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--name", default="bottle")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--device", default="0")
    ap.add_argument(
        "--python",
        default=os.environ.get("YOLO_PYTHON", "$HOME/.conda/envs/ai/bin/python"),
        help="python remote (default conda env 'ai')",
    )
    ap.add_argument("--skip-setup", action="store_true", help="lewati upload/extract dataset")
    ap.add_argument(
        "--force-upload",
        action="store_true",
        help="upload dataset walau sha remote sudah sama",
    )
    ap.add_argument("--foreground", action="store_true", help="stream log langsung (bukan nohup)")
    ap.add_argument("--poll", type=int, default=8, help="interval polling log (detik)")
    ap.add_argument("--timeout", type=int, default=7200, help="timeout training (detik)")
    ap.add_argument("--force", action="store_true", help="lanjut walau disk remote sempit")
    ap.add_argument("--dataset", default=str(DATASET), help="folder dataset lokal (images/labels train+val)")
    ap.add_argument("--class-name", default="bottle", help="nama kelas tunggal (lama)")
    ap.add_argument("--names", default="", help="daftar kelas dipisah koma (multi-kelas)")
    ap.add_argument("--out", default=str(WEIGHTS / "bottle_best.pt"))
    args = ap.parse_args()

    ds = Path(args.dataset)
    if args.names:
        names = [n.strip() for n in args.names.split(",") if n.strip()]
    elif (ds / "classes.txt").exists():
        names = [n.strip() for n in (ds / "classes.txt").read_text(encoding="utf-8").splitlines() if n.strip()]
    else:
        names = [args.class_name]
    ensure_dirs(WEIGHTS, RUNS)

    print(f"[1/6] cek koneksi {args.host}")
    who = ssh(args.host, "whoami; hostname; echo $HOME").splitlines()
    remote_user, remote_host, remote_home = who[0], who[1], who[2]
    print(f"      ok: {remote_user}@{remote_host} (home {remote_home})")
    try:
        gpu = ssh(args.host, "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader").strip()
        print(f"      GPU: {gpu}")
    except RuntimeError:
        raise SystemExit("Remote tidak punya nvidia-smi / GPU. Script ini butuh GPU NVIDIA.")

    proj = f"{remote_home}/{args.remote_dir}"
    dataset_remote = f"{proj}/dataset"
    ssh(args.host, f"mkdir -p {proj}/logs {dataset_remote}")

    free_kb = int(ssh(args.host, f"df -Pk {remote_home} | tail -1 | awk '{{print $4}}'").strip())
    free_gb = free_kb / 1024 / 1024
    print(f"      disk kosong remote: {free_gb:.1f} GB")
    if free_gb < 3 and not args.force:
        raise SystemExit("Disk remote < 3 GB. Bersihkan dulu atau pakai --force.")
    if free_gb < 8:
        print("      [warn] disk sempit; instalasi torch cu128 butuh ~6 GB")

    if args.skip_setup:
        print("[2/6] skip setup (--skip-setup)")
        print("[3/6] skip extract")
    else:
        local_sha = dataset_sha(ds)
        remote_sha = ssh(
            args.host, f"cat {dataset_remote}/.sha 2>/dev/null || true"
        ).strip()
        same = remote_sha == local_sha and not args.force_upload
        data_yaml = write_data_yaml(dataset_remote, names)
        scp(args.host, data_yaml, f"{proj}/data.yaml")
        scp(args.host, ROOT / "remote" / "train.sh", f"{proj}/train.sh")

        if same:
            print(f"[2/6] dataset remote identik (sha {local_sha}), upload dilewati")
            print("[3/6] skip extract (data.yaml + train.sh saja yang dikirim)")
        else:
            print(f"[2/6] pack + upload dataset (sha {local_sha})")
            archive = build_archive(ds)
            scp(args.host, archive, f"{proj}/{archive.name}")
            print("[3/6] extract di remote")
            out = ssh(
                args.host,
                f"cd {proj} && tar -xzf {archive.name} && rm -f {archive.name} && "
                f"sed -i 's/\\r$//' train.sh && chmod +x train.sh && "
                f"echo {local_sha} > dataset/.sha && "
                f"echo train=$(ls dataset/images/train | wc -l) val=$(ls dataset/images/val | wc -l)",
            )
            print(f"      {out.strip()}")

    if args.skip_setup:
        data_yaml = write_data_yaml(dataset_remote, names)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_name = f"train-{run_id}.log"
    env = (
        f"PROJ={proj} EPOCHS={args.epochs} PATIENCE={args.patience} "
        f"IMGSZ={args.imgsz} BATCH={args.batch} "
        f"MODEL={args.model} NAME={args.name} WORKERS={args.workers} DEVICE={args.device} "
        f'YOLO_PYTHON="{args.python}"'
    )

    if args.foreground:
        print(f"[4/6] training (foreground) di {proj}")
        code = subprocess.run(
            ssh_base(args.host) + [f"cd {proj} && {env} bash train.sh 2>&1 | tee logs/{log_name}"],
            text=True,
        ).returncode
        if code != 0:
            raise SystemExit(f"training gagal (exit {code}). Log: {proj}/logs/{log_name}")
        print("[4/6] training selesai")
    else:
        print(f"[4/6] start training (nohup, tahan disconnect) -> logs/{log_name}")
        pid = ssh(
            args.host,
            f"cd {proj} && {env} nohup bash train.sh > logs/{log_name} 2>&1 & echo $!",
        ).strip().splitlines()[-1]
        print(f"      pid {pid}, polling tiap {args.poll}s")

        printed = 0
        started = time.time()
        while True:
            time.sleep(args.poll)
            alive = ssh(
                args.host,
                f"kill -0 {pid} 2>/dev/null && echo RUN || echo DONE",
                tries=5,
            ).strip()
            chunk = ssh(
                args.host,
                f"tail -n +{printed + 1} {proj}/logs/{log_name} 2>/dev/null || true",
                tries=5,
            )
            lines = chunk.splitlines()
            if lines:
                for line in lines:
                    print(f"      | {line}")
                printed += len(lines)
            if alive == "DONE":
                break
            if time.time() - started > args.timeout:
                raise SystemExit(f"timeout {args.timeout}s. Log: {proj}/logs/{log_name}")
        print("[4/6] proses training selesai")

    best_remote = f"{proj}/runs/{args.name}/weights/best.pt"
    found = ssh(args.host, f"test -f {best_remote} && echo OK || echo MISSING").strip()
    if found != "OK":
        tail = ssh(args.host, f"tail -n 30 {proj}/logs/{log_name} 2>/dev/null || true")
        if tail.strip():
            print("[error] 30 baris log terakhir:")
            for line in tail.splitlines():
                print(f"      | {line}")
        raise SystemExit(
            "Training gagal: best.pt tidak ada. Cek log lengkap di "
            f"{proj}/logs/{log_name}"
        )

    print(f"[5/6] unduh weight -> {args.out}")
    out = Path(args.out)
    scp_down(args.host, best_remote, out)
    size_mb = out.stat().st_size / 1e6
    print(f"      {out} ({size_mb:.1f} MB)")

    local_run = RUNS / args.name
    ensure_dirs(local_run)
    for fname in ("results.csv", "results.png", "confusion_matrix.png", "val_batch0_pred.jpg"):
        try:
            scp_down(args.host, f"{proj}/runs/{args.name}/{fname}", local_run / fname)
        except RuntimeError:
            pass

    print("[6/6] ringkasan")
    csv_path = local_run / "results.csv"
    if csv_path.exists():
        rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
        if rows:
            last = {k.strip(): v for k, v in rows[-1].items()}
            m50 = last.get("metrics/mAP50(B)", "?")
            m5095 = last.get("metrics/mAP50-95(B)", "?")
            print(f"      epochs={len(rows)} mAP50={m50} mAP50-95={m5095}")
    print(f"      weight siap: {out}")
    print("      demo: python src/demo_yolo.py --weights " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
