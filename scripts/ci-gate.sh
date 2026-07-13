#!/usr/bin/env bash
# The local equivalent of the PR-to-master merge gate (implementation/build.md).
#
# Runs exactly the checks the `pr` GitHub Actions workflow runs, in one command,
# so an agent or human can reproduce the gate before pushing:
#   ruff format --check · ruff check · pyright · pytest (unit + component)
#   · OpenAPI spec drift · eslint · vitest · generated-client drift
#
# Invoke as `mise run gate` or `./scripts/ci-gate.sh`. Frontend steps are a
# clearly-labeled no-op until the P5 frontend workspace lands (see WEB_DIR).
set -euo pipefail

cd "$(dirname "$0")/.."
WEB_DIR="${WEB_DIR:-web}"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# --- Python quality (bzh:python-toolchain) ----------------------------------
step "ruff format --check ."
uv run ruff format --check .

step "ruff check ."
uv run ruff check .

step "pyright"
uv run pyright

# --- Python tests: unit + component tiers -----------------------------------
step "pytest (unit + component tiers)"
uv run pytest

# --- OpenAPI spec drift (bzh:generated-client, the Python half) --------------
# Regenerate the committed specs and fail on any difference. The frontend's
# openapi-ts client is generated from these; keeping them exact is what makes
# client drift impossible by construction.
step "OpenAPI spec drift: blizzard-export-openapi + git diff openapi/"
uv run blizzard-export-openapi --out-dir openapi >/dev/null
if ! git diff --quiet -- openapi/; then
  echo "ERROR: OpenAPI specs under openapi/ are stale — run 'mise run export-openapi' and commit." >&2
  git --no-pager diff -- openapi/ >&2
  exit 1
fi
echo "OK: committed OpenAPI specs match the exporter."

# --- Frontend: eslint + vitest + generated-client drift ----------------------
# Declared interface (P5 frontend builder owns it; integrate reconciles exact
# names): $WEB_DIR is the Angular workspace with `npm run lint` (eslint),
# `npm run test` (vitest), and `npm run generate:client` (openapi-ts codegen of
# the committed client). Guarded so its absence is a green no-op today.
if [ -f "$WEB_DIR/package.json" ]; then
  step "eslint ($WEB_DIR)"
  ( cd "$WEB_DIR" && npm ci && npm run lint )

  step "vitest ($WEB_DIR)"
  ( cd "$WEB_DIR" && npm run test )

  step "generated-client drift ($WEB_DIR): openapi-ts codegen + git diff"
  ( cd "$WEB_DIR" && npm run generate:client >/dev/null )
  if ! git diff --quiet -- "$WEB_DIR"; then
    echo "ERROR: the generated API client is stale — regenerate and commit." >&2
    git --no-pager diff --stat -- "$WEB_DIR" >&2
    exit 1
  fi
  echo "OK: committed generated client matches the exported specs."
else
  step "PENDING (P5 frontend builder): no $WEB_DIR/package.json — eslint, vitest, and generated-client drift are no-ops until the web workspace lands."
fi

step "Gate passed."
