"""device -- accelerator resolution for the model-backed engines.

The service runs on Linux + NVIDIA CUDA and on Apple Silicon Macs; deferred
engines fall back to CPU when no accelerator is present (slow but correct --
extraction runs once per document version, so time is not the binding
constraint, parser-consensus.md §Cost note). Preference order: cuda > mps >
cpu, overridable with INGESTION_DEVICE for pinning a specific device in
multi-GPU deployments (e.g. INGESTION_DEVICE=cuda:1).
"""

from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def resolve_device() -> str:
    """The torch device string every model-backed engine should load onto.

    "auto" (and an empty value) means *probe*, not a literal device: it's the
    documented default and `scripts/start_ingestion.sh` exports it explicitly,
    so it reaches here on a normal server start. Passing it through verbatim
    made every engine call `.to("auto")`, which raises RuntimeError -- and
    since engines treat a load failure as "unavailable, degrade gracefully",
    that silently dropped GLM-OCR from the consensus (single-parser, no error)
    on both CUDA and MPS boxes whenever the service was started via the script
    rather than by hand."""
    override = (os.environ.get("INGESTION_DEVICE") or "").strip()
    if override and override.lower() != "auto":
        return override
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"
