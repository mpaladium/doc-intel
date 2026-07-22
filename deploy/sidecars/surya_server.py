#!/usr/bin/env python3
"""Surya OCR sidecar -- reference implementation.

Runs in its OWN virtualenv (surya wants transformers>=4.51; the ingestion venv
resolves docling's 5.8.x). See deploy/sidecars/README.md.

Contract expected by app/pipeline/engines/surya.py:
    POST /            body = raw PNG bytes of a page/region crop
    200  {"text": "..."}      -> the candidate
    anything else / no key    -> the adapter records "no candidate" and moves on

    uv pip install "surya-ocr>=0.14" torch pillow
    python surya_server.py --port 8102

LICENCE: Surya's code is Apache-2.0, its weights are Rail-M (conditional
commercial terms). Running it here keeps those weights out of the ingestion
environment -- check the terms before production use.

Note this engine only ever contributes a candidate string under
Node.parsers["surya"]; the caller caps OCR-derived confidence and quarantines
OCR-derived Parameters by default, so nothing here can silently become a limit.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("surya_sidecar")

_lock = threading.Lock()
_state: dict = {"loaded": False, "rec": None, "det": None}


def _load():
    """Lazy one-time load so the server binds its port immediately.

    VERSION-SENSITIVE BLOCK. surya-ocr renamed its entry points across releases
    (`run_ocr` -> predictor classes). If your pinned version differs, this is the
    only function to adjust; the HTTP contract above is stable.
    """
    with _lock:
        if _state["loaded"]:
            return
        _state["loaded"] = True
        from surya.detection import DetectionPredictor
        from surya.recognition import RecognitionPredictor

        _state.update(rec=RecognitionPredictor(), det=DetectionPredictor())
        log.info("Surya predictors loaded")


def recognize_text(png: bytes) -> str | None:
    _load()
    rec, det = _state["rec"], _state["det"]
    if rec is None:
        return None
    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("RGB")
    predictions = rec([image], det_predictor=det)
    if not predictions:
        return None
    lines = [ln.text for ln in getattr(predictions[0], "text_lines", []) if ln.text]
    return "\n".join(lines).strip() or None


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            png = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            text = recognize_text(png)
            body = json.dumps({"text": text} if text else {}).encode()
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
        body = json.dumps({"ok": True, "engine": "surya"}).encode()
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
    ap.add_argument("--port", type=int, default=8102)
    ap.add_argument("--preload", action="store_true", help="load weights at startup")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.preload:
        _load()
    log.info("Surya sidecar on http://%s:%d  ->  INGESTION_SURYA_URL", args.host, args.port)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
