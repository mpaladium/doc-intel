"""Model-backed corroborator engines (parser-consensus.md deferred swap-ins).

Each engine is a lazily-loaded, gracefully-degrading adapter: if its weights or
runtime aren't available the engine reports unavailable and the pipeline
proceeds single-parser, exactly as before -- an engine NEVER becomes a hard
dependency of ingestion. When available, an engine contributes CANDIDATES to
`Node.parsers`; the consensus layer (consensus.py / assemble) compares them and
quarantines disagreement. Engines never overwrite the authority's output.

Two integration shapes:
  * IN-PROCESS (glm_ocr) -- loads via `transformers` in this venv, the same class
    docling already uses, so it adds no new runtime stack. DEFAULT-ON.
  * OUT-OF-PROCESS SIDECARS (mineru, surya) -- their model stacks pin
    `transformers` versions incompatible with docling's (and with each other), so
    they cannot share this venv (`uv lock` is unsatisfiable). They run as HTTP
    sidecars in their own environments; the adapters here are thin, dependency-
    free clients (see `_sidecar.py`). Enabled by pointing an env var at the
    service; unset/unreachable => the adapter reports unavailable.

Currently registered:
  * glm_ocr -- zai-org/GLM-OCR (0.9B, MIT weights / Apache-2.0 code), in-process,
    DEFAULT-ON, serving both lanes: formula recognition (96.5 UniMERNet -- the
    extract.equation gold-set metric) and scanned-page OCR (#1 OmniDocBench v1.5).
  * mineru -- MinerU's formula stage, UniMERNet (opendatalab/UniMERNet,
    Apache-2.0 code + weights), sidecar via INGESTION_MINERU_URL. A THIRD,
    architecturally-independent equation LaTeX candidate (encoder-decoder, not a
    VLM) next to Docling + GLM-OCR.
  * surya -- Surya OCR (datalab-to/surya, Apache-2.0 code; weights under a
    conditional-commercial Rail-M licence), sidecar via INGESTION_SURYA_URL. An
    independent scanned-OCR text candidate.

All add to (never replace) each other: more independent voters is the premise of
N-version consensus. Their names are already in consensus.GENUINE_TEXT_PARSERS /
TEXT_AUTHORITY_ORDER, so the consensus vote picks them up without a call-site
change (see docs/references/ocr-engine-evaluation.md).
"""
