#!/usr/bin/env bash
# Pyright's unnecessary-suppression check over the tests — the report naming every `# type: ignore` and
# `# pyright: ignore` comment that suppresses nothing. Generates a pyright config from the repo's `[tool.pyright]` with
# only `reportUnnecessaryTypeIgnoreComment = "error"` overridden; every other setting is the repo's own.
#
# Checks `tests` and both store-migration test trees, or the paths passed as arguments (flags such as `--outputjson`
# pass through). Exits non-zero while any comment is inert. Shipping code is excluded: the structural gate already
# holds it. Not part of the gate.
set -euo pipefail

cd "$(dirname "$0")/.."

has_path=false
for arg in "$@"; do
  case "$arg" in -*) ;; *) has_path=true ;; esac
done
if ! $has_path; then
  set -- "$@" tests src/blizzard/hub/store/migrations/tests src/blizzard/runner/store/migrations/tests
fi

CONFIG="$(mktemp pyrightconfig.unused-ignores.XXXXXX.json)"
trap 'rm -f "$CONFIG"' EXIT

python3 - "$CONFIG" <<'PY'
import json
import sys
import tomllib

with open("pyproject.toml", "rb") as f:
    settings = tomllib.load(f)["tool"]["pyright"]
settings.update(reportUnnecessaryTypeIgnoreComment="error")
with open(sys.argv[1], "w") as f:
    json.dump(settings, f)
PY

uv run pyright --project "$CONFIG" "$@"
