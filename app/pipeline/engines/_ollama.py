"""_ollama -- stdlib-only client for a vision model served by an Ollama server.

Ollama speaks a different protocol from the raw-PNG sidecars in `_sidecar.py`
(which POST image bytes and read one JSON key): it takes a JSON body with
base64-encoded images on `/api/generate` and answers `{"response": "..."}`.
That difference is why this is its own module rather than a flag on _sidecar.

Same graceful-degrade contract as every other engine transport: EVERY failure
mode (bad URL, connection refused, timeout, non-200, malformed JSON, missing
key, unencodable image) becomes `None`. An engine that returns no candidate is
a supported state -- an engine is never a hard dependency of ingestion.

Determinism (AGENTS/SKILLS model-backed skill rule): `temperature: 0` and a
fixed `seed`, so the same crop yields the same string across runs. Ollama's
default is sampled, which would make consensus non-reproducible.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import urllib.error
import urllib.request


def _timeout() -> float:
    """Shares INGESTION_SIDECAR_TIMEOUT with the raw-PNG sidecars -- one knob for
    'how long may a corroborator take', regardless of transport."""
    try:
        return float(os.environ.get("INGESTION_SIDECAR_TIMEOUT", "30"))
    except ValueError:
        return 30.0


def _generate_url(base: str) -> str:
    """Accept either a bare host root ("http://127.0.0.1:11434") or a full
    endpoint, so the env var can be set either way without surprise."""
    base = base.rstrip("/")
    return base if base.endswith("/api/generate") else f"{base}/api/generate"


def generate(base_url: str, model: str, prompt: str, image, log: logging.Logger,
             max_tokens: int = 512) -> str | None:
    """Run one vision prompt over a PIL image against an Ollama server.
    Returns the response text, or None on any failure -- never raises."""
    try:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:  # unencodable image -- treat as no candidate
        log.warning("ollama image encode failed (%s: %s)", type(exc).__name__, str(exc)[:200])
        return None

    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "images": [b64],
        "stream": False,
        "options": {"temperature": 0, "seed": 0, "num_predict": max_tokens},
    }).encode("utf-8")
    req = urllib.request.Request(_generate_url(base_url), data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        log.warning("ollama %s unavailable (%s: %s)", base_url, type(exc).__name__, str(exc)[:200])
        return None

    value = payload.get("response") if isinstance(payload, dict) else None
    return value.strip() or None if isinstance(value, str) else None


def reachable(base_url: str, model: str, log: logging.Logger) -> bool:
    """Whether the server answers and actually has `model` pulled. Checked once
    and cached by the caller: a configured-but-empty Ollama should degrade to
    'no candidate' with one clear log line, not fail per equation."""
    base = base_url.rstrip("/")
    if base.endswith("/api/generate"):
        base = base[: -len("/api/generate")]
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=_timeout()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        log.warning("ollama %s unreachable (%s: %s)", base_url, type(exc).__name__, str(exc)[:200])
        return False
    names = [m.get("name", "") for m in (payload.get("models") or [])
             if isinstance(m, dict)]
    # Ollama reports "glm-ocr:latest" for a "glm-ocr" pull; match on the stem.
    stem = model.split(":")[0]
    if any(n.split(":")[0] == stem for n in names):
        return True
    log.warning("ollama at %s has no model %r (pulled: %s) -- engine unavailable",
                base_url, model, ", ".join(names) or "none")
    return False
