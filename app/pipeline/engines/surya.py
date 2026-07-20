"""surya -- the Surya OCR corroborator engine (out-of-process).

Surya (datalab-to/surya) is the scanned/image-region OCR authority named in
`docs/references/parser-consensus.md`. Here it is wired as an INDEPENDENT text
transcriber on SCANNED/UNCERTAIN pages: a genuine second/third opinion next to
the RapidOCR layer and GLM-OCR, voting through `consensus.GENUINE_TEXT_PARSERS`.
Its independence (a dedicated detection+recognition OCR stack, not a general
VLM) is what makes agreement real corroboration and disagreement a real signal.

Why out-of-process: Surya's Python stack pins `transformers>=4.51` (2.x wants
5.12+) which conflicts with the base pipeline's docling-pinned `transformers`
5.8.x, and it is incompatible with the UniMERNet sidecar's pin too -- the three
model stacks cannot share a virtualenv. Surya 2.x already runs as its own
spawned inference server (vllm/llama.cpp), so a sidecar is its native shape.
Licensing also favors isolation: Surya CODE is Apache-2.0 but its WEIGHTS are
under a conditional-commercial Rail-M licence, so keeping it out of the base
install keeps the default build cleanly MIT/Apache.

Guarantees (parser-consensus.md OCR authority + "Surya confidence ceiling"): the
caller already caps OCR-derived confidence at 0.95 and quarantines any
OCR-derived Parameter by default; this engine only contributes a candidate
string under `Node.parsers["surya"]`, never a confidence or a repair.

Graceful, same contract as `glm_ocr`/`mineru`: if `INGESTION_SURYA_URL` is unset
or the sidecar is unreachable, `available()` is False and `recognize_text`
returns None -- the pipeline stays on its existing engines, never crashes.

Sidecar contract: `POST {INGESTION_SURYA_URL}` with the raw PNG bytes of the
page/region crop (Content-Type image/png); respond `200 {"text": "..."}`.
"""

from __future__ import annotations

import logging
import os

from app.pipeline.engines._sidecar import post_image

log = logging.getLogger("engines.surya")

ENGINE_NAME = "surya"


def _url() -> str | None:
    return os.environ.get("INGESTION_SURYA_URL") or None


def available() -> bool:
    """True iff a sidecar URL is configured (see mineru.available)."""
    return _url() is not None


def recognize_text(image) -> str | None:
    """Plain text for a page/region crop (PIL image). The OCR-lane candidate.
    None on any failure (never raises)."""
    url = _url()
    if url is None:
        return None
    return post_image(url, image, result_key="text", log=log)
