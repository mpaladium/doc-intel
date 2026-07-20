#!/usr/bin/env bash
# Opens the document picker for a directory of PDFs against an already-running
# ingestion-engine (see start_ingestion.sh). The picker at http://<host>:<port>/
# lists every PDF under the server's DOCS_DIR and lets you parse/view each one
# from the browser -- there's no separate UI process, the picker is just a route
# on the same server, so this script exists to make "look at these documents"
# a one-liner.
#
# DOCS_DIR is resolved once when the server starts (start_ingestion.sh's
# directory argument / the DOCS_DIR env var); a client script can't repoint a
# running server. So this script verifies the directory you pass matches what
# the running server is actually serving, and refuses to open a misleading
# listing if they differ.
#
# To parse a single arbitrary PDF (not necessarily under DOCS_DIR), POST it
# directly:  curl -X POST --data-binary @doc.pdf http://<host>:<port>/parse
#
# Usage:
#   ./scripts/start_ui.sh path/to/docs-dir
#   HOST=127.0.0.1 PORT=8001 ./scripts/start_ui.sh path/to/docs-dir
set -euo pipefail

: "${HOST:=127.0.0.1}"
: "${PORT:=8001}"
BASE_URL="http://${HOST}:${PORT}"

docs_dir="${1:-}"
if [[ -z "${docs_dir}" || ! -d "${docs_dir}" ]]; then
  echo "usage: $0 <path-to-docs-dir>" >&2
  exit 1
fi

if ! curl -sf -o /dev/null "${BASE_URL}/docs"; then
  echo "error: ingestion-engine not reachable at ${BASE_URL} -- start it with ./scripts/start_ingestion.sh" >&2
  exit 1
fi

# Resolve the input directory the same way the server resolves DOCS_DIR
# (Path(...).resolve() -- absolute, symlinks followed); `cd && pwd -P` matches
# that without depending on `realpath`.
resolved=$(cd "${docs_dir}" && pwd -P)

server_docs_dir=$(curl -s "${BASE_URL}/documents" | python3 -c "import sys,json; print(json.load(sys.stdin)['docs_dir'])")

if [[ "${resolved}" != "${server_docs_dir}" ]]; then
  echo "error: the running ingestion-engine is serving a different DOCS_DIR." >&2
  echo "  requested: ${resolved}" >&2
  echo "  serving:   ${server_docs_dir}" >&2
  echo "Restart the server against this directory instead of repointing a live one:" >&2
  echo "  ./scripts/start_ingestion.sh \"${docs_dir}\"" >&2
  echo "  (or: DOCS_DIR=\"${docs_dir}\" ./scripts/start_ingestion.sh)" >&2
  exit 1
fi

picker_url="${BASE_URL}/"
echo "[start_ui] document picker: ${picker_url}"

if command -v open >/dev/null 2>&1; then
  open "${picker_url}"          # macOS
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "${picker_url}"      # Linux desktop
else
  echo "[start_ui] open this URL manually: ${picker_url}"
fi
