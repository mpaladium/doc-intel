#!/usr/bin/env bash
# Parses a single PDF (from anywhere, not just DOCS_DIR) against an
# already-running ingestion-engine (see start_ingestion.sh), waits for the
# CanonicalEdition to be ready, and opens the confidence-sorted verification
# inspector in the default browser. The UI itself is just a route on the
# same server -- there's no separate UI process -- this script exists to
# make "look at the result" a one-liner.
#
# If your PDFs live under DOCS_DIR, you likely want the document picker at
# http://<host>:<port>/ instead, which lists every file there and lets you
# parse/view each one from the browser without the command line.
#
# Usage:
#   ./scripts/start_ui.sh path/to/document.pdf
#   HOST=127.0.0.1 PORT=8001 ./scripts/start_ui.sh path/to/document.pdf
set -euo pipefail

: "${HOST:=127.0.0.1}"
: "${PORT:=8001}"
BASE_URL="http://${HOST}:${PORT}"

pdf_path="${1:-}"
if [[ -z "${pdf_path}" || ! -f "${pdf_path}" ]]; then
  echo "usage: $0 <path-to-pdf>" >&2
  exit 1
fi

if ! curl -sf -o /dev/null "${BASE_URL}/docs"; then
  echo "error: ingestion-engine not reachable at ${BASE_URL} -- start it with ./scripts/start_ingestion.sh" >&2
  exit 1
fi

echo "[start_ui] POST ${pdf_path} -> ${BASE_URL}/parse"
key=$(curl -s -X POST --data-binary @"${pdf_path}" "${BASE_URL}/parse" | python3 -c "import sys,json; print(json.load(sys.stdin)['edition_id'])")

echo "[start_ui] waiting for edition ${key} ..."
for _ in $(seq 1 120); do
  status=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/editions/${key}")
  if [[ "${status}" == "200" ]]; then
    break
  fi
  sleep 1
done

ui_url="${BASE_URL}/editions/${key}/ui"
echo "[start_ui] ready: ${ui_url}"

if command -v open >/dev/null 2>&1; then
  open "${ui_url}"          # macOS
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "${ui_url}"      # Linux desktop
else
  echo "[start_ui] open this URL manually: ${ui_url}"
fi
