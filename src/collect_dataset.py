"""Capture frame webcam per kelas (TANPA pelabelan) — labeling dipisah ke GPU.

Tekan 1..9 = simpan frame saat itu ke folder kelas tersebut. Tidak ada deteksi /
Moondream di sini, jadi capture tetap 30 fps dan offline. Label dibuat belakangan
oleh `src/label_remote.py` di server GPU.

Struktur output:
    dataset/capture/classes.txt
    dataset/capture/prompts.txt
    dataset/capture/manifest.csv
    dataset/capture/<kelas>/frame_YYYYmmdd_HHMMSS_fff.jpg

Contoh:
    python src/collect_dataset.py
    python src/collect_dataset.py --classes bottle,ballpoint_box,computer_mouse
    python src/collect_dataset.py --source video.mp4 --auto-save 1 --class bottle

Lanjutkan: python src/label_remote.py  (lihat docs/CAPTURE_LABELING.md)
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2

from common import ROOT, add_root_to_path, ensure_dirs, open_capture

add_root_to_path()

DEFAULT_CLASSES = "bottle,ballpoint_box,computer_mouse"


def parse_classes(text: str) -> list[str]:
    return [c.strip() for c in text.split(",") if c.strip()]


def default_prompts(classes: list[str]) -> dict[str, str]:
    return {c: c.replace("_", " ") for c in classes}


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
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture frame per kelas (tanpa label)")
    ap.add_argument("--source", default="0", help="index webcam / video / folder gambar")
    ap.add_argument("--classes", default=DEFAULT_CLASSES, help="daftar kelas dipisah koma")
    ap.add_argument(
        "--prompts", default="",
        help='override prompt labeling, contoh "bottle=bottle,computer_mouse=computer mouse"',
    )
    ap.add_argument("--out", default=str(ROOT / "dataset" / "capture"))
    ap.add_argument("--class", dest="kelas", default="", help="kelas awal (nama atau nomor 1..9)")
    ap.add_argument("--auto-save", type=float, default=0.0, help="auto-save tiap N detik")
    ap.add_argument("--max", type=int, default=0, help="batas total gambar (0 = tanpa batas)")
    ap.add_argument("--cooldown", type=float, default=0.25, help="jeda minimum antar simpan (detik)")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--no-window", action="store_true")
    args = ap.parse_args()

    classes = parse_classes(args.classes)
    if not 1 <= len(classes) <= 9:
        raise SystemExit("--classes harus 1..9 nama")

    out = Path(args.out)
    ensure_dirs(out)
    (out / "classes.txt").write_text("\n".join(classes) + "\n", encoding="utf-8")
    prompts_path = out / "prompts.txt"
    prompts = read_prompts(prompts_path) or default_prompts(classes)
    prompts.update(parse_prompts(args.prompts))
    prompts = {c: prompts.get(c, c.replace("_", " ")) for c in classes}
    prompts_path.write_text(
        "\n".join(f"{c}={p}" for c, p in prompts.items()) + "\n", encoding="utf-8"
    )
    for c in classes:
        ensure_dirs(out / c)

    aktif = 0
    if args.kelas:
        if args.kelas.isdigit() and 1 <= int(args.kelas) <= len(classes):
            aktif = int(args.kelas) - 1
        elif args.kelas in classes:
            aktif = classes.index(args.kelas)
        else:
            raise SystemExit(f"kelas '{args.kelas}' tidak ada di {classes}")

    source = int(args.source) if str(args.source).isdigit() else args.source
    cap = open_capture(source)
    if isinstance(source, int):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    meta_path = out / "manifest.csv"
    baru = not meta_path.exists()
    fh = meta_path.open("a", newline="", encoding="utf-8")
    writer = csv.writer(fh)
    if baru:
        writer.writerow(["file", "kelas", "waktu", "source", "frame"])

    saved = 0
    frames = 0
    per_kelas: Counter = Counter()
    auto = args.auto_save > 0
    last_save = 0.0
    last_status = ""

    def simpan(frame, idx: int, paksa: bool = False) -> bool:
        nonlocal saved, last_save, last_status
        now = time.time()
        if not paksa and now - last_save < args.cooldown:
            return False
        if args.max and saved >= args.max:
            return False
        kelas = classes[idx]
        stamp = datetime.now()
        name = f"frame_{stamp:%Y%m%d_%H%M%S_%f}"[:-3]
        cv2.imwrite(
            str(out / kelas / f"{name}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]
        )
        writer.writerow([f"{kelas}/{name}.jpg", kelas, stamp.isoformat(timespec="seconds"),
                         str(source), frames])
        fh.flush()
        saved += 1
        per_kelas[kelas] += 1
        last_save = now
        last_status = f"simpan {kelas} ({per_kelas[kelas]}) {'[paksa]' if paksa else ''}"
        print(f"[capture] {last_status} -> {kelas}/{name}.jpg")
        return True

    print(f"[capture] kelas: {', '.join(f'{i + 1}={n}' for i, n in enumerate(classes))}")
    print(f"[capture] source={source} out={out}")
    print("[capture] prompt labeling: " + ", ".join(f"{c}='{p}'" for c, p in prompts.items()))
    print("[capture] tanpa deteksi/label di sini; labeling menyusul via src/label_remote.py")
    if not args.no_window:
        print("[capture] tombol: 1..9 simpan kelas itu | spasi simpan kelas aktif | a auto | q keluar")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[capture] frame habis / kamera terputus")
                break
            frames += 1
            h, w = frame.shape[:2]
            kelas_aktif = classes[aktif]

            if args.max and saved >= args.max:
                last_status = f"target {args.max} gambar tercapai"
                if not args.no_window:
                    cv2.imshow("capture dataset", frame)
                    cv2.waitKey(300)
                break

            if auto and time.time() - last_save >= args.auto_save:
                simpan(frame, aktif)

            if not args.no_window:
                view = frame.copy()
                hud = (
                    f"kelas {aktif + 1}/{len(classes)}: {kelas_aktif}  "
                    f"tersimpan {saved} (kls ini {per_kelas[kelas_aktif]})"
                )
                cv2.putText(view, hud, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                            (0, 235, 255), 2, cv2.LINE_AA)
                info = "label nanti di GPU | prompt: " + prompts[kelas_aktif]
                if auto:
                    info += f" | AUTO {args.auto_save:.1f}s"
                cv2.putText(view, info, (12, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                            (200, 200, 200), 1, cv2.LINE_AA)
                cv2.putText(
                    view, "  ".join(
                        f"{i + 1}={n}:{per_kelas[n]}" for i, n in enumerate(classes)
                    )[:118],
                    (12, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1,
                    cv2.LINE_AA,
                )
                if last_status:
                    cv2.putText(view, last_status, (12, 86), cv2.FONT_HERSHEY_SIMPLEX,
                                0.55, (0, 255, 0), 1)
                cv2.imshow("capture dataset", view)
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    break
                if ord("1") <= key <= ord("9"):
                    idx = key - ord("1")
                    if idx < len(classes):
                        aktif = idx
                        simpan(frame, idx, paksa=True)
                elif key == ord(" "):
                    simpan(frame, aktif, paksa=True)
                elif key == ord("a"):
                    auto = not auto
                    print(f"[capture] auto-save {'ON' if auto else 'OFF'}")
            if args.max_frames and frames >= args.max_frames:
                break
    finally:
        cap.release()
        fh.close()
        if not args.no_window:
            cv2.destroyAllWindows()

    rincian = ", ".join(f"{k}={v}" for k, v in per_kelas.items()) or "-"
    print(f"[capture] selesai: {saved} gambar ({rincian}), {frames} frame.")
    print(f"[capture] berikutnya: python src/label_remote.py --source {out}")
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
