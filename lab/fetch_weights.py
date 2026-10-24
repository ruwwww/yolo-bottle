"""Tarik weight hasil training dari server remote ke folder weights/ lokal."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from common import ROOT, WEIGHTS, ensure_dirs, load_env


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description="Fetch best.pt dari remote via scp")
    ap.add_argument("--host", default=os.environ.get("YOLO_SSH_HOST", "100.91.215.116"))
    ap.add_argument("--remote-dir", default=os.environ.get("YOLO_REMOTE_DIR", "yolo-bottle"))
    ap.add_argument("--run", default="bottle", help="nama run training")
    ap.add_argument("--which", default="best.pt", choices=["best.pt", "last.pt"])
    ap.add_argument("--out", default=str(WEIGHTS / "bottle_best.pt"))
    args = ap.parse_args()

    ensure_dirs(WEIGHTS)
    home = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", args.host, "echo $HOME"],
        text=True,
        capture_output=True,
    ).stdout.strip()
    if not home:
        raise SystemExit(f"SSH ke {args.host} gagal")

    remote = f"{home}/{args.remote_dir}/runs/{args.run}/weights/{args.which}"
    out = Path(args.out)
    print(f"[fetch] {args.host}:{remote} -> {out}")
    code = subprocess.run(["scp", "-o", "BatchMode=yes", f"{args.host}:{remote}", str(out)]).returncode
    if code != 0:
        raise SystemExit("scp gagal. Cek apakah training sudah selesai di remote.")
    print(f"[fetch] ok ({out.stat().st_size / 1e6:.1f} MB)")
    print(f"[fetch] demo: python src/demo_yolo.py --weights {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
