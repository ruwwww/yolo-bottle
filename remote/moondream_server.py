"""Moondream Photon HTTP server: text-guided bounding boxes on GPU.

Jalankan di server GPU (lihat scripts/8_moondream_server.ps1):
  ~/.conda/envs/ai/bin/python -m uvicorn moondream_server:app --host 0.0.0.0 --port 8090

Endpoint:
  GET  /health                       -> {"ok": true, "model": ..., "device": ...}
  POST /detect {"image": base64_jpg,
                "prompt": "bottle",
                "resize": 960}       -> {"objects": [{x_min,y_min,x_max,y_max,label}], "ms": 42}
  POST /caption {"image": ..., "length": "normal"} -> {"caption": ..., "ms": ...}
  POST /query   {"image": ..., "question": "..."}  -> {"answer": ..., "ms": ...}
"""
from __future__ import annotations

import base64
import io
import os
import threading
import time

from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel

import moondream as md

MODEL_ID = os.environ.get("MD_MODEL", "moondream2")
PRINT_LABEL = os.environ.get("MD_LABEL", "air_mineral")

app = FastAPI(title="moondream-bbox")
_lock = threading.Lock()
_t0 = time.time()
model = md.photon(MODEL_ID)
print(f"[md] model {MODEL_ID} siap ({time.time() - _t0:.1f}s)")


class DetectReq(BaseModel):
    image: str
    prompt: str = "bottle"
    resize: int = 0
    label: str = ""


class ImageReq(BaseModel):
    image: str


class CaptionReq(ImageReq):
    length: str = "normal"


class QueryReq(ImageReq):
    question: str = "What is in this image?"


def _decode(image_b64: str) -> Image.Image:
    try:
        raw = base64.b64decode(image_b64, validate=True)
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"gambar tidak valid: {exc}") from exc


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL_ID, "uptime": round(time.time() - _t0, 1)}


@app.post("/detect")
def detect(req: DetectReq):
    img = _decode(req.image)

    if req.resize and max(img.size) > req.resize:
        scale = req.resize / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)

    t = time.time()
    with _lock:
        out = model.detect(img, req.prompt)
    ms = (time.time() - t) * 1000
    label = req.label or PRINT_LABEL
    objs = [
        {
            "label": label,
            "x_min": o["x_min"], "y_min": o["y_min"],
            "x_max": o["x_max"], "y_max": o["y_max"],
        }
        for o in out.get("objects", [])
    ]
    return {"objects": objs, "ms": round(ms, 1), "prompt": req.prompt, "size": list(img.size)}


@app.post("/caption")
def caption(req: CaptionReq):
    img = _decode(req.image)
    t = time.time()
    with _lock:
        out = model.caption(img, length=req.length)
    return {
        "caption": out.get("caption", ""),
        "ms": round((time.time() - t) * 1000, 1),
        "size": list(img.size),
    }


@app.post("/query")
def query(req: QueryReq):
    img = _decode(req.image)
    t = time.time()
    with _lock:
        out = model.query(img, req.question)
    return {
        "answer": out.get("answer", ""),
        "ms": round((time.time() - t) * 1000, 1),
        "size": list(img.size),
    }
