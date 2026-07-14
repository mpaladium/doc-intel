#!/usr/bin/env bash
# Starts the ingestion-engine API + verification UI (same FastAPI app serves
# both -- see app/api.py). Works unmodified on a Mac dev machine and on a
# Linux box with an NVIDIA GPU: device selection (CUDA/MPS/CPU) is handled by
# app/pipeline/extract_docling.py via INGESTION_DEVICE=auto (default).
#
# The first argument (or DOCS_DIR env var) is a directory of PDFs to browse
# and parse from the document picker at GET /. To pre-process a whole
# directory in the background instead of one-at-a-time from the UI, use
# scripts/evaluate_dir.sh.
#
# Usage:
#   ./scripts/start_ingestion.sh /path/to/pdfs           # http://127.0.0.1:8001
#   DOCS_DIR=/path/to/pdfs ./scripts/start_ingestion.sh
#   HOST=0.0.0.0 PORT=8080 ./scripts/start_ingestion.sh /path/to/pdfs
#   INGESTION_DEVICE=cpu ./scripts/start_ingestion.sh /path/to/pdfs   # force CPU on a shared GPU box
#   INGESTION_RELOAD=1 ./scripts/start_ingestion.sh /path/to/pdfs     # dev: auto-restart on source changes
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ "${1:-}" != "" ]]; then
  DOCS_DIR="$1"
fi

: "${HOST:=127.0.0.1}"
: "${PORT:=8001}"
: "${ARTIFACT_STORE_PATH:=./data/artifacts}"
: "${DOCS_DIR:=./data/docs}"
: "${INGESTION_DEVICE:=auto}"
: "${INGESTION_MAX_CONCURRENT_PARSES:=1}"
: "${INGESTION_RELOAD:=0}"

export ARTIFACT_STORE_PATH DOCS_DIR INGESTION_DEVICE INGESTION_MAX_CONCURRENT_PARSES

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is not installed -- see https://docs.astral.sh/uv/" >&2
  exit 1
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "[start_ingestion] NVIDIA GPU detected ($(nvidia-smi -L | head -1))"
elif [[ "$(uname -s)" == "Darwin" ]]; then
  echo "[start_ingestion] running on macOS (CPU/MPS)"
else
  echo "[start_ingestion] no NVIDIA GPU detected -- running on CPU"
fi

echo "[start_ingestion] docs dir:      ${DOCS_DIR}"
echo "[start_ingestion] artifact store: ${ARTIFACT_STORE_PATH}"
echo "[start_ingestion] serving API + UI on http://${HOST}:${PORT}  (document picker: /)"

# Build the full command as an array and append to it, rather than
# expanding a possibly-empty array on its own -- bash 3.2 (macOS's default
# /bin/bash) mishandles "${EMPTY_ARRAY[@]}" under `set -u`.
CMD=(uv run uvicorn app.api:app --host "${HOST}" --port "${PORT}")

if [[ "${INGESTION_RELOAD}" == "1" ]]; then
  # --reload-dir only accepts directories (a bare `canonical_schema.py`
  # entry is silently dropped by uvicorn/watchfiles), so this watches the
  # project root instead and excludes everything that isn't source: the
  # artifact store and DOCS_DIR both default under ./data and both mutate on
  # every parse, which would otherwise thrash the reloader; .venv/.git/caches
  # are just noise. Excludes are defensive if a custom DOCS_DIR/
  # ARTIFACT_STORE_PATH points elsewhere under the tree too.
  CMD+=(--reload --reload-dir .
    --reload-exclude 'data/*' --reload-exclude '.venv/*' --reload-exclude '.git/*'
    --reload-exclude '__pycache__/*' --reload-exclude '.pytest_cache/*'
    --reload-exclude 'tests/fixtures/*.pdf')
  echo "[start_ingestion] hot reload: ON (watching source files, excluding data/.venv/caches)"
  echo "[start_ingestion] note: each reload re-runs Docling model warm-up (a few seconds)"
fi

exec "${CMD[@]}"
