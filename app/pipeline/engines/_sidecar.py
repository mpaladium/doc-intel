"""_sidecar -- the shared, dependency-free HTTP client for out-of-process
corroborator engines (mineru, surya).

Those engines' model stacks can't share the base virtualenv (incompatible
`transformers` pins), so they run as sidecar services in their own environments
and are reached over HTTP. This helper POSTs a crop and returns the recognized
string, converting EVERY failure mode (unset/bad URL, connection refused,
timeout, non-200, malformed JSON, missing key) into `None` -- an engine
contributing "no candidate" is the graceful-degrade contract the whole engine
layer relies on (an engine is never a hard dependency of ingestion).

stdlib only (`urllib`) so adding an engine adds no runtime dependency. The
image is sent as raw PNG bytes; the sidecar answers `{"<result_key>": "..."}`.
"""

from __future__ import annotations

import io
import json
import logging
import os
import urllib.error
import urllib.request


def _timeout() -> float:
    try:
        return float(os.environ.get("INGESTION_SIDECAR_TIMEOUT", "30"))
    except ValueError:
        return 30.0


def _png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def post_image(url: str, image, result_key: str, log: logging.Logger) -> str | None:
    """POST a PIL image (as PNG) to a sidecar and return response[result_key].
    None on any failure -- never raises."""
    try:
        body = _png_bytes(image)
    except Exception as exc:  # unencodable image -- treat as no candidate
        log.warning("sidecar image encode failed (%s: %s)", type(exc).__name__, str(exc)[:200])
        return None
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "image/png"})
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        log.warning("sidecar %s unavailable (%s: %s)", url, type(exc).__name__, str(exc)[:200])
        return None
    # A well-behaved sidecar answers {"<result_key>": "<string>"}; anything else
    # (a JSON list, a missing/non-string value) is "no candidate", never a crash.
    value = payload.get(result_key) if isinstance(payload, dict) else None
    return value.strip() or None if isinstance(value, str) else None
