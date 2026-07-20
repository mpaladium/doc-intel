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
    """The torch device string every model-backed engine should load onto."""
    override = os.environ.get("INGESTION_DEVICE")
    if override:
        return override
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"
