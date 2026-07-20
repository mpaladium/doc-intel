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

Lazy + graceful: the model loads once on first use (resolve_device: cuda > mps
> cpu); if the weights aren't cached, the architecture isn't supported by the
installed transformers, or INGESTION_GLM_OCR gates it off, `available()` is
False and every recognize_* call returns None -- the pipeline stays
single-parser, never crashes, never blocks.

Determinism: greedy decode (do_sample=False), pinned model id -- same image in,
same string out (AGENTS/SKILLS model-backed skill rule).
"""

from __future__ import annotations

import logging
import os
import threading

from app.pipeline.device import resolve_device

log = logging.getLogger("engines.glm_ocr")

MODEL_ID = "zai-org/GLM-OCR"
ENGINE_NAME = "glm_ocr"

_lock = threading.Lock()
_state: dict = {"tried": False, "model": None, "processor": None}


def _enabled() -> bool:
    return os.environ.get("INGESTION_GLM_OCR", "1").lower() not in ("0", "false", "no")


def _load():
    """One-time lazy load; failure is remembered so a missing model is one log
    line, not a retry storm."""
    with _lock:
        if _state["tried"]:
            return
        _state["tried"] = True
        if not _enabled():
            log.info("GLM-OCR gated off (INGESTION_GLM_OCR)")
            return
        try:
            from transformers import AutoModelForImageTextToText, AutoProcessor
            device = resolve_device()
            processor = AutoProcessor.from_pretrained(MODEL_ID)
            model = AutoModelForImageTextToText.from_pretrained(
                MODEL_ID, dtype="auto").to(device).eval()
            _state["model"], _state["processor"] = model, processor
            log.info("GLM-OCR loaded on %s", device)
        except Exception as exc:  # missing weights / unsupported arch / OOM
            log.warning("GLM-OCR unavailable (%s: %s) -- pipeline continues single-parser",
                        type(exc).__name__, str(exc)[:200])


def available() -> bool:
    _load()
    return _state["model"] is not None


def _recognize(image, prompt: str, max_new_tokens: int) -> str | None:
    """Run one recognition prompt over a PIL image region. Greedy, offline."""
    if not available():
        return None
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
