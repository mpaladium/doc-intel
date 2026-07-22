#!/usr/bin/env bash
# Brings up the whole ingestion stack in one command: starts whichever
# corroborator engines can be started, validates the ones it can't start
# (Ollama-backed GLM-OCR), prints an unambiguous status table, then starts the
# API + UI via start_ingestion.sh.
#
# Why this exists: the engines degrade silently by design -- an engine that
# can't answer contributes "no candidate" and ingestion continues. That's the
# right runtime behavior, but it means a crashed sidecar or a typo'd URL looks
# exactly like a healthy setup. Worse, mineru/surya `available()` only checks
# that the env var is SET (reachability is proven per-call), so exporting a URL
# for a dead sidecar reads as "configured" while contributing nothing. This
# script therefore probes every engine for real and exports a URL only after
# that probe succeeds.
#
# Everything is optional. Nothing here is required to ingest a document -- a
# stack with zero corroborators is a supported (single-parser) configuration.
#
# Usage:
#   ./scripts/start_stack.sh /path/to/pdfs          # start everything available + the server
#   ./scripts/start_stack.sh --check                # probe + print the table, start nothing
#   ./scripts/start_stack.sh --no-sidecars /path    # server + GLM-OCR only
#   ./scripts/start_stack.sh --sidecars-only        # corroborators only, no API server
#                                                   # (e.g. before scripts/evaluate_dir.sh)
#   MINERU_PORT=9101 SURYA_VENV=~/envs/surya ./scripts/start_stack.sh /path
#
# Any other arguments are passed through to start_ingestion.sh unchanged.
# See deploy/sidecars/README.md for engine setup.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

: "${MINERU_PORT:=8101}"
: "${SURYA_PORT:=8102}"
: "${MINERU_VENV:=${HOME}/.venvs/unimernet}"
: "${SURYA_VENV:=${HOME}/.venvs/surya}"
: "${OLLAMA_URL:=http://127.0.0.1:11434}"
: "${INGESTION_GLM_OCR:=1}"
: "${INGESTION_GLM_OCR_MODEL:=glm-ocr}"
: "${INGESTION_GLM_OCR_AUTO:=1}"
: "${SIDECAR_START_TIMEOUT:=30}"

check_only=false
start_sidecars=true
sidecars_only=false
PASSTHROUGH=()
for arg in "$@"; do
  case "${arg}" in
    --check)         check_only=true ;;
    --no-sidecars)   start_sidecars=false ;;
    --sidecars-only) sidecars_only=true ;;
    *)               PASSTHROUGH+=("${arg}") ;;
  esac
done

log() { echo "[stack] $*"; }

# Status lines are collected and printed together at the end, so the table is
# one contiguous block rather than interleaved with startup chatter.
STATUS_LINES=()
EQUATION_LANE=()
OCR_LANE=()
STARTED_PIDS=()          # only sidecars WE started -- adopted ones are never killed
SERVER_PID=""

status() { STATUS_LINES+=("$(printf '  %-8s %-12s %s' "$1" "$2" "$3")"); }

# Kill a process AND its descendants. Necessary because start_ingestion.sh execs
# `uv run uvicorn`, and `uv` spawns the real server as a CHILD -- killing only
# the pid we launched reaps the wrapper and orphans the uvicorn holding the
# port. `pgrep -P` exists on both macOS and Linux.
kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "${pid}" 2>/dev/null || true); do
    kill_tree "${child}"
  done
  kill "${pid}" 2>/dev/null || true
}

cleanup() {
  local pid
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill_tree "${SERVER_PID}"
  fi
  for pid in ${STARTED_PIDS+"${STARTED_PIDS[@]}"}; do
    if kill -0 "${pid}" 2>/dev/null; then
      log "stopping sidecar pid ${pid}"
      kill_tree "${pid}"
    fi
  done
}
trap cleanup EXIT INT TERM

# A sidecar is "up" when it answers GET / with our health payload. Checking for
# `"ok"` (not just a 200) avoids adopting an unrelated service that happens to
# be listening on the port.
probe_sidecar() {
  curl -sf --max-time 3 "$1/" 2>/dev/null | grep -q '"ok"'
}

wait_for_sidecar() {
  local url="$1" deadline=$(( SECONDS + SIDECAR_START_TIMEOUT ))
  while (( SECONDS < deadline )); do
    probe_sidecar "${url}" && return 0
    sleep 1
  done
  return 1
}

# engine, port, venv, server script, url-env-var
setup_sidecar() {
  local name="$1" port="$2" venv="$3" server="$4" url_var="$5"
  local url="http://127.0.0.1:${port}"
  local preset="${!url_var:-}"

  # 1. URL already configured -> validate and adopt; never manage its lifecycle.
  if [[ -n "${preset}" ]]; then
    if probe_sidecar "${preset}"; then
      status "${name}" "AVAILABLE" "sidecar ${preset} (preconfigured)"
      return 0
    fi
    # Deliberately UNSET it: leaving a dead URL exported is the silent-no-vote
    # failure this script exists to prevent.
    unset "${url_var}"
    status "${name}" "UNAVAILABLE" "${preset} not answering -- URL unset so it can't read as configured"
    return 1
  fi

  # 2. Something already healthy on the port -> adopt (idempotent re-runs).
  if probe_sidecar "${url}"; then
    export "${url_var}=${url}"
    status "${name}" "AVAILABLE" "sidecar ${url} (adopted, already running)"
    return 0
  fi

  if [[ "${start_sidecars}" != "true" ]]; then
    status "${name}" "SKIPPED" "--no-sidecars"
    return 1
  fi
  if [[ "${check_only}" == "true" ]]; then
    status "${name}" "UNAVAILABLE" "not running (--check starts nothing)"
    return 1
  fi
  if [[ ! -x "${venv}/bin/python" ]]; then
    status "${name}" "UNAVAILABLE" "venv ${venv} not found -- see deploy/sidecars/README.md"
    return 1
  fi

  # 3. Start it ourselves.
  local log_dir="./data/sidecar-logs"
  mkdir -p "${log_dir}"
  local log_file="${log_dir}/${name}-$(date +%Y%m%d-%H%M%S).log"
  nohup "${venv}/bin/python" "${server}" --host 127.0.0.1 --port "${port}" \
    > "${log_file}" 2>&1 &
  local pid=$!
  disown 2>/dev/null || true      # survive this shell (see --sidecars-only)
  STARTED_PIDS+=("${pid}")

  if wait_for_sidecar "${url}"; then
    export "${url_var}=${url}"
    status "${name}" "AVAILABLE" "sidecar ${url} (pid ${pid}, log ${log_file})"
    return 0
  fi
  kill "${pid}" 2>/dev/null || true
  status "${name}" "UNAVAILABLE" "did not become ready in ${SIDECAR_START_TIMEOUT}s -- see ${log_file}"
  return 1
}

setup_glm_ocr() {
  if [[ "${INGESTION_GLM_OCR}" =~ ^(0|false|no)$ ]]; then
    status "glm_ocr" "DISABLED" "INGESTION_GLM_OCR=${INGESTION_GLM_OCR}"
    return 1
  fi

  local url="${INGESTION_GLM_OCR_URL:-}" auto=false
  if [[ -z "${url}" && "${INGESTION_GLM_OCR_AUTO}" != "0" ]]; then
    url="${OLLAMA_URL}"
    auto=true
  fi

  if [[ -n "${url}" ]]; then
    # Reuse the engine's own validator: it checks the server answers AND that
    # the model is actually pulled, rather than reimplementing /api/tags here.
    if uv run python -c "
import logging, sys
from app.pipeline.engines import _ollama
logging.disable(logging.CRITICAL)
sys.exit(0 if _ollama.reachable('${url}', '${INGESTION_GLM_OCR_MODEL}', logging.getLogger()) else 1)
" 2>/dev/null; then
      export INGESTION_GLM_OCR_URL="${url}"
      export INGESTION_GLM_OCR_MODEL
      local how="configured"
      [[ "${auto}" == "true" ]] && how="auto-detected"
      status "glm_ocr" "AVAILABLE" "ollama ${url} (model ${INGESTION_GLM_OCR_MODEL}, ${how})"
      # Selecting a backend from ambient state must never be silent.
      [[ "${auto}" == "true" ]] && log "auto-detected Ollama at ${url} -- using it for GLM-OCR (INGESTION_GLM_OCR_AUTO=0 to disable)"
      return 0
    fi
    if [[ "${auto}" == "false" ]]; then
      unset INGESTION_GLM_OCR_URL
      status "glm_ocr" "UNAVAILABLE" "ollama ${url} unreachable or missing model '${INGESTION_GLM_OCR_MODEL}'"
      return 1
    fi
  fi

  # In-process: NOT probed on purpose -- available() would load the weights in a
  # throwaway process, and the server would then load them again.
  status "glm_ocr" "IN-PROCESS" "transformers, loads on first use (no Ollama detected)"
  return 0
}

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is not installed -- see https://docs.astral.sh/uv/" >&2
  exit 1
fi

setup_glm_ocr && { EQUATION_LANE+=("glm_ocr"); OCR_LANE+=("glm_ocr"); } || true
setup_sidecar mineru "${MINERU_PORT}" "${MINERU_VENV}" \
  deploy/sidecars/mineru_server.py INGESTION_MINERU_URL && EQUATION_LANE+=("mineru") || true
setup_sidecar surya "${SURYA_PORT}" "${SURYA_VENV}" \
  deploy/sidecars/surya_server.py INGESTION_SURYA_URL && OCR_LANE+=("surya") || true

log "corroborator status:"
for line in "${STATUS_LINES[@]}"; do echo "[stack] ${line}"; done

join_lane() {
  # Not `IFS=", "; echo "$*"` -- $* only uses IFS's FIRST character.
  local out="" item
  for item in "$@"; do out="${out:+${out}, }${item}"; done
  echo "${out:-none}"
}
log "equation lane: $(join_lane ${EQUATION_LANE+"${EQUATION_LANE[@]}"})"
log "OCR lane:      $(join_lane ${OCR_LANE+"${OCR_LANE[@]}"})  (scanned/uncertain pages only -- idle on a born-digital corpus)"
log "an engine only votes where its lane runs; confirm per document via Node.parsers (deploy/sidecars/README.md)"

if [[ "${check_only}" == "true" ]]; then
  log "--check: nothing started"
  exit 0
fi

if [[ "${sidecars_only}" == "true" ]]; then
  # Hand the sidecars over to the caller: clearing STARTED_PIDS disarms the
  # cleanup trap, which would otherwise kill the very processes this mode exists
  # to leave running.
  if [[ ${#STARTED_PIDS[@]} -gt 0 ]]; then
    log "sidecars left running (pids: ${STARTED_PIDS[*]}) -- stop with: kill ${STARTED_PIDS[*]}"
  fi
  STARTED_PIDS=()
  log "--sidecars-only: API server not started. Export these for another process:"
  [[ -n "${INGESTION_GLM_OCR_URL:-}" ]] && echo "  export INGESTION_GLM_OCR_URL=${INGESTION_GLM_OCR_URL}"
  [[ -n "${INGESTION_MINERU_URL:-}" ]] && echo "  export INGESTION_MINERU_URL=${INGESTION_MINERU_URL}"
  [[ -n "${INGESTION_SURYA_URL:-}" ]]  && echo "  export INGESTION_SURYA_URL=${INGESTION_SURYA_URL}"
  exit 0
fi

# Background + `wait` rather than exec-or-foreground. exec would replace this
# shell and lose the trap entirely; a FOREGROUND child is worse than it looks --
# bash defers trap handlers until the foreground command finishes, so a SIGTERM
# to this script would leave the sidecars running until the server happened to
# exit. `wait` is interruptible, so the trap fires immediately and tears down
# the server and every sidecar we started.
./scripts/start_ingestion.sh ${PASSTHROUGH+"${PASSTHROUGH[@]}"} &
SERVER_PID=$!
wait "${SERVER_PID}" || true
