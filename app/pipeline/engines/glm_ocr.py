"""glm_ocr -- the GLM-OCR corroborator engine (zai-org/GLM-OCR).

A 0.9B-parameter multimodal OCR model (MIT weights, Apache-2.0 code) run
in-process via plain `transformers` `AutoModelForImageTextToText` -- the same
class Docling already loads for CodeFormula, so no new runtime stack. Native
prompts: "Formula Recognition:" (LaTeX out; 96.5 on UniMERNet, the
extract.equation gold-set benchmark) and "Text Recognition:".

Roles in the consensus architecture (parser-consensus.md):
  * EQUATION corroborator -- a second, independent LaTeX candidate next to
    Docling CodeFormula's; disagreement routes the equation to a human, which
    is the spec's intended behavior ("flag the difference and let a human
    resolve it").
  * SCANNED-page OCR corroborator -- an independent text candidate next to
    RapidOCR's layer, subject to the OCR confidence ceiling; OCR consensus
    activates through GENUINE_TEXT_PARSERS.

Two interchangeable backends, same candidates either way:
  * IN-PROCESS (default) -- `transformers` in this venv, on resolve_device().
  * OLLAMA -- set `INGESTION_GLM_OCR_URL` (e.g. http://127.0.0.1:11434) to call
    a server running https://ollama.com/library/glm-ocr instead. Useful when the
    GPU box already runs Ollama, or to keep model weights out of this venv.
    `INGESTION_GLM_OCR_MODEL` overrides the model name (default "glm-ocr").
    Selected purely by whether the URL is set, so no code change to switch.

Lazy + graceful: the backend initializes once on first use; if the weights
aren't cached, the architecture isn't supported by the installed transformers,
the Ollama server is unreachable or lacks the model, or INGESTION_GLM_OCR gates
it off, `available()` is False and every recognize_* call returns None -- the
pipeline stays single-parser, never crashes, never blocks.

Determinism: greedy decode (do_sample=False), pinned model id -- same image in,
same string out (AGENTS/SKILLS model-backed skill rule).
"""

from __future__ import annotations

import logging
import os
import threading

from app.pipeline.device import resolve_device
from app.pipeline.engines import _ollama

log = logging.getLogger("engines.glm_ocr")

MODEL_ID = "zai-org/GLM-OCR"
ENGINE_NAME = "glm_ocr"
OLLAMA_MODEL_DEFAULT = "glm-ocr"

_lock = threading.Lock()
_state: dict = {"tried": False, "model": None, "processor": None, "ollama": None}


def _enabled() -> bool:
    return os.environ.get("INGESTION_GLM_OCR", "1").lower() not in ("0", "false", "no")


def _ollama_url() -> str | None:
    """Set => use the Ollama backend instead of loading in-process."""
    return os.environ.get("INGESTION_GLM_OCR_URL") or None


def _ollama_model() -> str:
    return os.environ.get("INGESTION_GLM_OCR_MODEL") or OLLAMA_MODEL_DEFAULT


def _load():
    """One-time lazy init of whichever backend is configured; failure is
    remembered so a missing model is one log line, not a retry storm."""
    with _lock:
        if _state["tried"]:
            return
        _state["tried"] = True
        if not _enabled():
            log.info("GLM-OCR gated off (INGESTION_GLM_OCR)")
            return

        url = _ollama_url()
        if url:
            # Probe once: a configured-but-empty Ollama must degrade to "no
            # candidate" with one clear line, not fail on every equation.
            if _ollama.reachable(url, _ollama_model(), log):
                _state["ollama"] = (url, _ollama_model())
                log.info("GLM-OCR via ollama at %s (model %s)", url, _ollama_model())
            return

        try:
            from transformers import AutoModelForImageTextToText, AutoProcessor
            device = resolve_device()
            processor = AutoProcessor.from_pretrained(MODEL_ID)
            model = AutoModelForImageTextToText.from_pretrained(
                MODEL_ID, dtype="auto").to(device).eval()
            _state["model"], _state["processor"] = model, processor
            log.info("GLM-OCR loaded in-process on %s", device)
        except Exception as exc:  # missing weights / unsupported arch / OOM
            log.warning("GLM-OCR unavailable (%s: %s) -- pipeline continues single-parser",
                        type(exc).__name__, str(exc)[:200])


def available() -> bool:
    _load()
    return _state["model"] is not None or _state["ollama"] is not None


def _recognize(image, prompt: str, max_new_tokens: int) -> str | None:
    """Run one recognition prompt over a PIL image region. Greedy, offline."""
    if not available():
        return None
    if _state["ollama"] is not None:
        url, model_name = _state["ollama"]
        return _ollama.generate(url, model_name, prompt, image, log,
                                max_tokens=max_new_tokens)
    import torch
    model, processor = _state["model"], _state["processor"]
    messages = [{"role": "user", "content": [
        {"type": "image", "image": image},
        {"type": "text", "text": prompt},
    ]}]
    try:
        inputs = processor.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors="pt").to(model.device)
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        text = processor.decode(out[0][inputs["input_ids"].shape[1]:],
                                skip_special_tokens=True)
        return text.strip() or None
    except Exception as exc:
        log.warning("GLM-OCR inference failed (%s: %s)", type(exc).__name__, str(exc)[:200])
        return None


def recognize_formula(image) -> str | None:
    """LaTeX for a formula region crop (PIL image). The equation-lane candidate."""
    return _recognize(image, "Formula Recognition:", max_new_tokens=512)


def recognize_text(image) -> str | None:
    """Plain text for a page/region crop (PIL image). The OCR-lane candidate."""
    return _recognize(image, "Text Recognition:", max_new_tokens=2048)
