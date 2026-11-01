#!/usr/bin/env bash
# Pyright in strict mode over everything — the report a strict-typing slice of the tests reads its before and after
# from. The gate already holds shipping code to strict (`[tool.pyright]` `strict`); this report extends strict to the
# tests. A file's own `# pyright: standard` header outranks the generated config, so a waived store migration test is
# measured by deleting its header and running the gate.
#
# Runs the repo's `[tool.pyright]` settings with `typeCheckingMode = "strict"` and `enableTypeIgnoreComments = false`,
# so a `# type: ignore` hides nothing. Checks the configured include paths, or the paths passed as arguments. Not part
# of the gate.
#
# `PYRIGHT_SETTINGS` (a JSON object) replaces the strict overrides above — `unused-ignores.sh` reuses this script that
# way.
set -euo pipefail

cd "$(dirname "$0")/.."

CONFIG="$(mktemp pyrightconfig.strict.XXXXXX.json)"
trap 'rm -f "$CONFIG"' EXIT

python3 - "$CONFIG" <<'PY'
import json
import os
import sys
import tomllib

with open("pyproject.toml", "rb") as f:
    settings = tomllib.load(f)["tool"]["pyright"]
overrides = os.environ.get("PYRIGHT_SETTINGS")
if overrides:
    settings.update(json.loads(overrides))
else:
    settings.update(typeCheckingMode="strict", enableTypeIgnoreComments=False)
with open(sys.argv[1], "w") as f:
    json.dump(settings, f)
PY

uv run pyright --project "$CONFIG" "$@"
