#!/usr/bin/env bash
# Batch-processes every PDF under a directory in the background, writing to
# the same content-addressed artifact store the API server reads from
# (app/cli/evaluate_dir.py does the actual work -- this script just detaches
# it so a large batch doesn't tie up your terminal). Once it's done (or even
# partway through), reload the document picker at http://<host>:<port>/ to
# see results per-document.
#
# Usage:
#   ./scripts/evaluate_dir.sh /path/to/pdfs
#   EVAL_WORKERS=2 ./scripts/evaluate_dir.sh /path/to/pdfs   # careful on a single GPU -- see README
#   ./scripts/evaluate_dir.sh /path/to/pdfs --foreground     # run inline instead of backgrounding
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

docs_dir="${1:-}"
if [[ -z "${docs_dir}" || ! -d "${docs_dir}" ]]; then
  echo "usage: $0 <path-to-pdf-dir> [--foreground]" >&2
  exit 1
fi
shift || true

foreground=false
if [[ "${1:-}" == "--foreground" ]]; then
  foreground=true
fi

: "${ARTIFACT_STORE_PATH:=./data/artifacts}"
: "${EVAL_WORKERS:=1}"
export ARTIFACT_STORE_PATH

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is not installed -- see https://docs.astral.sh/uv/" >&2
  exit 1
fi

log_dir="./data/eval-logs"
mkdir -p "${log_dir}"
timestamp=$(date +"%Y%m%d-%H%M%S")
log_file="${log_dir}/eval-${timestamp}.log"
pid_file="${log_dir}/eval-${timestamp}.pid"

echo "[evaluate_dir] docs dir:      ${docs_dir}"
echo "[evaluate_dir] artifact store: ${ARTIFACT_STORE_PATH}"
echo "[evaluate_dir] workers:        ${EVAL_WORKERS}"
echo "[evaluate_dir] log file:       ${log_file}"

if [[ "${foreground}" == "true" ]]; then
  exec uv run python -m app.cli.evaluate_dir "${docs_dir}" --workers "${EVAL_WORKERS}"
fi

nohup uv run python -m app.cli.evaluate_dir "${docs_dir}" --workers "${EVAL_WORKERS}" \
  > "${log_file}" 2>&1 &
bg_pid=$!
echo "${bg_pid}" > "${pid_file}"
disown

echo "[evaluate_dir] started in background, pid=${bg_pid}"
echo "[evaluate_dir] tail progress:  tail -f ${log_file}"
echo "[evaluate_dir] check running:  kill -0 ${bg_pid} && echo running || echo done"
