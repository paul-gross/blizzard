#!/usr/bin/env bash
# Pyright's unnecessary-suppression check over the tests — the report naming every `# type: ignore` and
# `# pyright: ignore` comment that suppresses nothing. Reuses the generated-config mechanism of `typecheck-strict.sh`
# and overrides only `reportUnnecessaryTypeIgnoreComment = "error"`; every other setting is the repo's
# `[tool.pyright]`.
#
# Checks `tests` and both store-migration test trees, or the paths passed as arguments (flags such as `--outputjson`
# pass through). Exits non-zero while any comment is inert. Shipping code is excluded: the structural gate already holds it. Not part of the gate.
set -euo pipefail

cd "$(dirname "$0")/.."

has_path=false
for arg in "$@"; do
  case "$arg" in -*) ;; *) has_path=true ;; esac
done
if ! $has_path; then
  set -- "$@" tests src/blizzard/hub/store/migrations/tests src/blizzard/runner/store/migrations/tests
fi

PYRIGHT_SETTINGS='{"reportUnnecessaryTypeIgnoreComment": "error"}' exec scripts/typecheck-strict.sh "$@"
