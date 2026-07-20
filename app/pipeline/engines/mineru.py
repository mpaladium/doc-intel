"""mineru -- the MinerU/UniMERNet equation corroborator engine (out-of-process).

MinerU's formula-recognition stage is UniMERNet (opendatalab/UniMERNet): a
Donut/Nougat-style vision-encoder-decoder that maps a formula-region image to
LaTeX (Apache-2.0 code + weights `wanderkid/unimernet_base`).

Why out-of-process: UniMERNet 0.2.3 hard-pins `transformers==4.42.4`, which is
incompatible with the base pipeline's `transformers` (docling resolves 5.8.x) --
they cannot share a virtualenv (verified: `uv lock` is unsatisfiable). So this
engine runs as a SIDECAR in its own environment and is reached over HTTP; the
adapter here is a thin, dependency-free (stdlib `urllib`) client. This is the
standard way to compose mutually-incompatible model stacks and keeps the base
build clean-licensed and light -- ingestion never hard-depends on it.

Role in the consensus architecture (parser-consensus.md "Equations"):
  * EQUATION corroborator -- a THIRD, INDEPENDENT LaTeX candidate next to
    Docling CodeFormula (the authority) and GLM-OCR. Its independence is the
    point: an encoder-decoder trained on formula crops, an architecture
    genuinely distinct from GLM-OCR's general VLM, so agreement is real
    corroboration and a disagreement (after `canon_equation.eq_compare_form`
    folding) is a real signal that routes the equation to a human -- the loser
    is never discarded, the authority is never overwritten.

Graceful, exactly like `glm_ocr`: if `INGESTION_MINERU_URL` is unset or the
sidecar is unreachable, `available()` is False and `recognize_formula` returns
None -- the pipeline stays on its existing engines, never crashes, never blocks.

Determinism is the sidecar's responsibility (pinned model id, greedy decode);
this client just relays the crop and returns the LaTeX verbatim.

Sidecar contract: `POST {INGESTION_MINERU_URL}` with the raw PNG bytes of the
formula crop (Content-Type image/png); respond `200 {"latex": "..."}`. A
reference server implementation lives with the deployment recipes, not in this
clean-licensed base package.
"""

from __future__ import annotations

import logging
import os

from app.pipeline.engines._sidecar import post_image

log = logging.getLogger("engines.mineru")

ENGINE_NAME = "mineru"


def _url() -> str | None:
    return os.environ.get("INGESTION_MINERU_URL") or None


def available() -> bool:
    """True iff a sidecar URL is configured. Reachability is proven per-call
    (recognize_formula returns None on any transport error), so a momentarily
    down sidecar degrades to 'no candidate', never a crash."""
    return _url() is not None


def recognize_formula(image) -> str | None:
    """LaTeX for a formula-region crop (PIL image). The equation-lane candidate.
    None on any failure (unset URL, transport error, bad response) -- never
    raises."""
    url = _url()
    if url is None:
        return None
    return post_image(url, image, result_key="latex", log=log)
