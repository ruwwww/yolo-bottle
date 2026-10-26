"""Web UI sederhana untuk Moondream Photon (server GPU).

UI ini hanya klien: semua inference tetap di moondream_server.py (:8090),
jadi tidak menambah pemakaian VRAM.

Jalankan di server:
  GRADIO_SERVER_NAME=0.0.0.0 GRADIO_SERVER_PORT=7860 \
    ~/.conda/envs/ai/bin/python moondream_ui.py
"""
from __future__ import annotations

import base64
import io
import os

import gradio as gr
import requests
from PIL import Image, ImageDraw

API = os.environ.get("MD_API", "http://127.0.0.1:8090")
TIMEOUT = float(os.environ.get("MD_UI_TIMEOUT", "120"))


def _b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def _post(path: str, payload: dict) -> dict:
    r = requests.post(f"{API}{path}", json=payload, timeout=TIMEOUT)
    if r.status_code != 200:
        raise gr.Error(f"{path} -> HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def _annotate(img: Image.Image, objects: list[dict], prompt: str) -> Image.Image:
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    w, h = out.size
    for o in objects:
        box = (o["x_min"] * w, o["y_min"] * h, o["x_max"] * w, o["y_max"] * h)
        draw.rectangle(box, outline="red", width=max(2, w // 300))
        draw.text((box[0] + 4, box[1] + 2), prompt, fill="red")
    return out


def do_detect(img, prompt, resize):
    if img is None:
        raise gr.Error("upload gambar dulu")
    data = _post("/detect", {"image": _b64(img), "prompt": prompt, "resize": int(resize)})
    objs = data.get("objects", [])
    rows = [f"- ({o['x_min']:.3f}, {o['y_min']:.3f}) → ({o['x_max']:.3f}, {o['y_max']:.3f})" for o in objs]
    status = f"**{len(objs)} objek** · {data['ms']} ms · prompt `{prompt}`\n" + ("\n".join(rows) if rows else "_tidak ada objek_")
    return _annotate(img, objs, prompt), status


def do_caption(img, length):
    if img is None:
        raise gr.Error("upload gambar dulu")
    data = _post("/caption", {"image": _b64(img), "length": length})
    return f"{data['caption']}\n\n_{data['ms']} ms_"


def do_query(img, question):
    if img is None:
        raise gr.Error("upload gambar dulu")
    data = _post("/query", {"image": _b64(img), "question": question})
    return f"{data['answer']}\n\n_{data['ms']} ms_"


with gr.Blocks(title="Moondream Photon") as demo:
    gr.Markdown("# Moondream Photon (RTX 5060 Ti)\nKlien tipis ke `moondream_server.py` · model `moondream2`")
    with gr.Tab("Detect (bbox)"):
        with gr.Row():
            d_img = gr.Image(type="pil", label="Gambar")
            d_out = gr.Image(type="pil", label="Hasil", interactive=False)
        with gr.Row():
            d_prompt = gr.Textbox(value="bottle", label="Prompt", scale=3)
            d_resize = gr.Slider(0, 1280, value=960, step=32, label="Resize maks (0=asli)")
        d_btn = gr.Button("Detect", variant="primary")
        d_status = gr.Markdown()
        d_btn.click(do_detect, [d_img, d_prompt, d_resize], [d_out, d_status])
        d_prompt.submit(do_detect, [d_img, d_prompt, d_resize], [d_out, d_status])

    with gr.Tab("Caption"):
        with gr.Row():
            c_img = gr.Image(type="pil", label="Gambar")
            c_txt = gr.Markdown()
        c_len = gr.Radio(["short", "normal", "long"], value="normal", label="Panjang")
        c_btn = gr.Button("Caption", variant="primary")
        c_btn.click(do_caption, [c_img, c_len], c_txt)

    with gr.Tab("Tanya"):
        with gr.Row():
            q_img = gr.Image(type="pil", label="Gambar")
            q_txt = gr.Markdown()
        q_box = gr.Textbox(value="Apa isi gambar ini?", label="Pertanyaan")
        q_btn = gr.Button("Tanya", variant="primary")
        q_btn.click(do_query, [q_img, q_box], q_txt)
        q_box.submit(do_query, [q_img, q_box], q_txt)

if __name__ == "__main__":
    demo.queue().launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")),
    )
