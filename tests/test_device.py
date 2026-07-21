"""Accelerator resolution (`app/pipeline/device.py`).

The regression these guard: `INGESTION_DEVICE=auto` is the documented default
AND `scripts/start_ingestion.sh` exports it explicitly, so it reaches
`resolve_device()` on any normal server start. It used to be returned verbatim,
so every model-backed engine called `.to("auto")` -> RuntimeError. Engines
treat a load failure as "unavailable, degrade gracefully", so this silently
dropped GLM-OCR out of the N-version consensus -- no error surfaced, the
pipeline just quietly ran single-parser on both CUDA and MPS boxes.
"""

from __future__ import annotations

import app.pipeline.device as device


def _resolve(monkeypatch, value):
    """resolve_device is lru_cached; clear it so each case really re-resolves."""
    if value is None:
        monkeypatch.delenv("INGESTION_DEVICE", raising=False)
    else:
        monkeypatch.setenv("INGESTION_DEVICE", value)
    device.resolve_device.cache_clear()
    try:
        return device.resolve_device()
    finally:
        device.resolve_device.cache_clear()


def test_auto_probes_instead_of_becoming_a_literal_device(monkeypatch):
    # "auto" must never reach torch -- it is not a valid device string.
    for value in ("auto", "AUTO", "  auto  ", ""):
        assert _resolve(monkeypatch, value) in ("cuda", "mps", "cpu"), value


def test_unset_probes(monkeypatch):
    assert _resolve(monkeypatch, None) in ("cuda", "mps", "cpu")


def test_explicit_device_is_passed_through(monkeypatch):
    # Pinning a specific GPU in a multi-GPU deployment must still work.
    assert _resolve(monkeypatch, "cuda:1") == "cuda:1"
    assert _resolve(monkeypatch, "cpu") == "cpu"


def test_resolved_device_is_loadable_by_torch(monkeypatch):
    """The end-to-end property that actually broke: whatever we return must be
    something torch can move a module onto."""
    import torch

    resolved = _resolve(monkeypatch, "auto")
    torch.nn.Linear(2, 2).to(resolved)  # raises if resolution returned junk
