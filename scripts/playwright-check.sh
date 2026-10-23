#!/usr/bin/env bash
# The Playwright package's static checks — what the gate's `playwright static` job runs, reproduced locally.
#
# Playwright runs TypeScript without checking its types, so the gate does: tsc, eslint, and a regenerate-and-diff of
# the committed wire types. Starts no daemon.
set -euo pipefail

cd "$(dirname "$0")/../tests"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# The install is a pure function of the lockfile, so a stamp of its hash makes the skip as trustworthy as the install.
step "npm ci (tests) — skipped when node_modules already matches the lockfile"
LOCK_HASH="$(sha256sum package-lock.json | cut -d' ' -f1)"
STAMP="node_modules/.package-lock-hash"
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$LOCK_HASH" ]; then
  echo "OK: node_modules matches package-lock.json — skipping npm ci."
else
  npm ci
  printf '%s' "$LOCK_HASH" > "$STAMP"
fi

step "typecheck (tests)"
npm run typecheck

step "lint (tests)"
npm run lint

step "wire types: openapi-ts codegen + git diff"
npm run generate:wire >/dev/null
if ! git diff --quiet -- harness/client/wire/; then
  echo "ERROR: tests/harness/client/wire/ differs from what's staged (working tree vs index, not vs the last commit)." >&2
  echo "        Run 'npm run generate:wire' in tests/, then stage the regenerated types." >&2
  git --no-pager diff --stat -- harness/client/wire/ >&2
  exit 1
fi
if [ -n "$(git ls-files --others --exclude-standard -- harness/client/wire/)" ]; then
  echo "ERROR: the generator wrote wire files that are not tracked:" >&2
  git ls-files --others --exclude-standard -- harness/client/wire/ >&2
  exit 1
fi
echo "OK: committed wire types match the exported spec."
