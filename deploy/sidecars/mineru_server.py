#!/usr/bin/env python3
"""MinerU / UniMERNet formula sidecar -- reference implementation.

Runs in its OWN virtualenv (UniMERNet hard-pins transformers==4.42.4, which the
ingestion venv cannot satisfy). See deploy/sidecars/README.md.

Contract expected by app/pipeline/engines/mineru.py:
    POST /            body = raw PNG bytes of a formula crop
    200  {"latex": "..."}     -> the candidate
    anything else / no key    -> the adapter records "no candidate" and moves on

The adapter converts EVERY failure into "no candidate", so this server is free
to answer 500 or die entirely without breaking ingestion.

    uv pip install "transformers==4.42.4" torch pillow unimernet
    python mineru_server.py --port 8101

Determinism is THIS server's responsibility (the adapter relays verbatim):
pinned model id, greedy decode, no sampling.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("mineru_sidecar")

MODEL_ID = "wanderkid/unimernet_base"

_lock = threading.Lock()
_state: dict = {"loaded": False, "model": None, "processor": None, "device": "cpu"}


def _load():
    """Lazy one-time load so the server binds its port immediately.

    VERSION-SENSITIVE BLOCK. `unimernet` has changed its Python API across
    releases; if yours differs, this function is the only thing to adjust. The
    HTTP contract above is what the pipeline depends on and does not change.
    """
    with _lock:
        if _state["loaded"]:
            return
        _state["loaded"] = True
        import torch
        from transformers import VisionEncoderDecoderModel, AutoProcessor

        device = ("cuda" if torch.cuda.is_available()
                  else "mps" if torch.backends.mps.is_available() else "cpu")
        processor = AutoProcessor.from_pretrained(MODEL_ID)
        model = VisionEncoderDecoderModel.from_pretrained(MODEL_ID).to(device).eval()
        _state.update(model=model, processor=processor, device=device)
        log.info("UniMERNet loaded on %s", device)


def recognize_latex(png: bytes) -> str | None:
    _load()
    model, processor, device = _state["model"], _state["processor"], _state["device"]
    if model is None:
        return None
    import torch
    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("RGB")
    inputs = processor(images=image, return_tensors="pt").to(device)
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=512, do_sample=False, num_beams=1)
    text = processor.batch_decode(out, skip_special_tokens=True)[0]
    return text.strip() or None


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            png = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            latex = recognize_latex(png)
            body = json.dumps({"latex": latex} if latex else {}).encode()
            code = 200
        except Exception as exc:                      # never take ingestion down
            log.warning("recognition failed (%s: %s)", type(exc).__name__, exc)
            body, code = json.dumps({"error": str(exc)[:200]}).encode(), 500
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                  # trivial health probe
        body = json.dumps({"ok": True, "model": MODEL_ID}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8101)
    ap.add_argument("--preload", action="store_true", help="load weights at startup")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.preload:
        _load()
    log.info("UniMERNet sidecar on http://%s:%d  ->  INGESTION_MINERU_URL", args.host, args.port)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
