#!/usr/bin/env bash
# Section-role gold-set gate (ARCHITECTURE.md §2.3 / build-order step 3): runs
# the pipeline on every hand-labeled gold doc under
# tests/goldsets/section_roles/ and checks the false-exclusion rate is 0 --
# a normative clause silently marked compliance_relevant=false would drop
# real compliance evidence, the worst failure mode this system has.
#
# Usage:
#   ./scripts/section_role_gold.sh
#   ./scripts/section_role_gold.sh --docs-dir /path/to/pdfs
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is not installed -- see https://docs.astral.sh/uv/" >&2
  exit 1
fi

exec uv run python -m app.cli.section_role_gold "$@"
