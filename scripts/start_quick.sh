#!/usr/bin/env bash
set -euo pipefail

# start_quick.sh — Bootstrap + start the full stack (sidecars + server + UI) in one command.
#
# Usage:
#   ./scripts/start_quick.sh /path/to/pdfs              # On Linux: auto-install sidecars, start everything
#   ./scripts/start_quick.sh /path/to/pdfs              # On macOS: skip install, start what's available
#   MINERU_VENV=/custom/path ./scripts/start_quick.sh /path/to/pdfs
#
# On Linux with no existing sidecar venvs, this creates ~/.venvs/{unimernet,surya}
# with pinned deps (transformers==4.42.4 for MinerU, surya-ocr>=0.14 for Surya),
# idempotently. Then starts start_stack.sh (sidecars + server) in the background
# and start_ui.sh (browser UI or URL) once the server is ready.
#
# On macOS, skips the install step (platform unsupported) and proceeds to start
# the server + UI; sidecars will be unavailable but the stack still works.
#
# Ctrl+C tears down cleanly: the trap propagates SIGINT/SIGTERM into start_stack.sh's
# own cleanup handler, which tears down both the server and any sidecars it started.

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "[quickstart] error: uv is not installed -- see https://docs.astral.sh/uv/" >&2
  exit 1
fi

# Parse args
if [[ $# -lt 1 ]]; then
  echo "Usage: $(basename "$0") <pdf_dir> [env vars...]" >&2
  exit 1
fi

PDF_DIR="$1"
if [[ ! -d "${PDF_DIR}" ]]; then
  echo "[quickstart] error: PDF directory '${PDF_DIR}' not found" >&2
  exit 1
fi

# Defaults (same as start_stack.sh)
: "${MINERU_VENV:=${HOME}/.venvs/unimernet}"
: "${SURYA_VENV:=${HOME}/.venvs/surya}"
: "${HOST:=127.0.0.1}"
: "${PORT:=8001}"
: "${SERVER_START_TIMEOUT:=60}"

export MINERU_VENV SURYA_VENV HOST PORT

_bootstrap_sidecar() {
  local name="$1"
  local venv="$2"
  local install_cmd="$3"

  if [[ -x "${venv}/bin/python" ]]; then
    echo "[quickstart] ${name} venv already exists at ${venv} -- skipping install"
    return 0
  fi

  echo "[quickstart] bootstrapping ${name} venv at ${venv}..."
  if ! uv venv "${venv}" --python 3.11; then
    echo "[quickstart] error: failed to create ${name} venv -- install may be incomplete" >&2
    return 1
  fi

  if ! uv pip install --python "${venv}/bin/python" ${install_cmd}; then
    echo "[quickstart] error: failed to install ${name} deps -- ${name} will be unavailable" >&2
    return 1
  fi

  echo "[quickstart] ${name} bootstrap complete"
  return 0
}

# Platform detection
PLATFORM="$(uname -s)"
if [[ "${PLATFORM}" == "Darwin" ]]; then
  echo "[quickstart] automated sidecar install is Linux-only (unsupported platform: ${PLATFORM}) -- see deploy/sidecars/README.md for manual setup"
else
  # Linux: bootstrap MinerU and Surya venvs, idempotently
  _bootstrap_sidecar "MinerU" "${MINERU_VENV}" \
    '"transformers==4.42.4" torch pillow unimernet' || true

  _bootstrap_sidecar "Surya" "${SURYA_VENV}" \
    '"surya-ocr>=0.14" torch pillow' || true
fi

# Start the stack in the background
echo "[quickstart] starting stack (sidecars + server) at http://${HOST}:${PORT}"
./scripts/start_stack.sh "${PDF_DIR}" &
STACK_PID=$!

# Trap so Ctrl+C propagates into start_stack.sh's cleanup
trap 'kill "${STACK_PID}" 2>/dev/null; wait "${STACK_PID}" 2>/dev/null; exit 130' INT TERM EXIT

# Poll for server readiness (same check as start_ui.sh:33-36)
BASE_URL="http://${HOST}:${PORT}"
READY=0
for i in $(seq 1 "${SERVER_START_TIMEOUT}"); do
  if curl -sf -o /dev/null "${BASE_URL}/docs" 2>/dev/null; then
    READY=1
    break
  fi
  sleep 1
done

if [[ ${READY} -eq 0 ]]; then
  echo "[quickstart] error: server did not become ready in ${SERVER_START_TIMEOUT}s" >&2
  kill "${STACK_PID}" 2>/dev/null || true
  wait "${STACK_PID}" 2>/dev/null || true
  exit 1
fi

echo "[quickstart] server ready, opening UI..."
./scripts/start_ui.sh "${PDF_DIR}"

# Wait for stack cleanup (trap handles Ctrl+C)
wait "${STACK_PID}"
