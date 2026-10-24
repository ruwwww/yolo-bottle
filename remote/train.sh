#!/usr/bin/env bash
# Dijalankan di mesin remote (Ubuntu + GPU NVIDIA), tanpa sudo.
# Default: pakai conda env "ai". Set YOLO_PYTHON untuk override.
# Env: PROJ EPOCHS IMGSZ BATCH MODEL NAME WORKERS DEVICE YOLO_PYTHON
set -euo pipefail

PROJ="${PROJ:-$HOME/yolo-bottle}"
EPOCHS="${EPOCHS:-100}"
PATIENCE="${PATIENCE:-30}"
IMGSZ="${IMGSZ:-640}"
BATCH="${BATCH:-8}"
MODEL="${MODEL:-yolo11n.pt}"
NAME="${NAME:-bottle}"
WORKERS="${WORKERS:-4}"
DEVICE="${DEVICE:-0}"
export PATH="$HOME/.local/bin:$PATH"
# ultralytics memanggil MLflow; file backend perlu opt-out eksplisit.
export MLFLOW_ALLOW_FILE_STORE="${MLFLOW_ALLOW_FILE_STORE:-true}"

cd "$PROJ"

PY="${YOLO_PYTHON:-$HOME/.conda/envs/ai/bin/python}"

if [ -x "$PY" ] && "$PY" -c "import ultralytics" >/dev/null 2>&1; then
  echo "[setup] pakai conda env: $PY"
  "$PY" - <<'PY'
import torch, ultralytics
print(f"[setup] torch {torch.__version__} cuda {torch.version.cuda} "
      f"gpu={torch.cuda.is_available()} ultralytics {ultralytics.__version__}")
PY
else
  echo "[setup] conda env tidak ditemukan, fallback ke venv + uv"
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
  if [ ! -f .venv/.uv-ok ]; then
    rm -rf .venv
    uv venv --python 3.12 .venv || uv venv .venv
    touch .venv/.uv-ok
  fi
  PY="$PROJ/.venv/bin/python"
  if ! "$PY" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
    uv pip install --python "$PY" --no-cache torch torchvision \
      --index-url https://download.pytorch.org/whl/cu128
  fi
  "$PY" -c "import ultralytics" 2>/dev/null || uv pip install --python "$PY" --no-cache ultralytics
fi

YOLO_BIN="$(dirname "$PY")/yolo"
echo "[train] mulai: model=$MODEL epochs=$EPOCHS imgsz=$IMGSZ batch=$BATCH device=$DEVICE"
if [ -x "$YOLO_BIN" ]; then
  "$YOLO_BIN" train \
    model="$MODEL" data="$PROJ/data.yaml" epochs="$EPOCHS" patience="$PATIENCE" imgsz="$IMGSZ" \
    batch="$BATCH" project="$PROJ/runs" name="$NAME" exist_ok=True \
    workers="$WORKERS" device="$DEVICE" cache=ram
else
  "$PY" - <<PY
from ultralytics import YOLO
YOLO("$MODEL").train(
    data="$PROJ/data.yaml", epochs=$EPOCHS, imgsz=$IMGSZ, batch=$BATCH,
    project="$PROJ/runs", name="$NAME", exist_ok=True, patience=$PATIENCE,
    workers=$WORKERS, device="$DEVICE", cache="ram",
)
PY
fi

echo "[train] selesai. weights: $PROJ/runs/$NAME/weights/best.pt"
