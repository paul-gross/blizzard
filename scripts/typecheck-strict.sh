#!/usr/bin/env bash
# Pyright in strict mode — the report a strict-typing slice reads its before and after from.
#
# Runs the repo's `[tool.pyright]` settings with `typeCheckingMode = "strict"` and `enableTypeIgnoreComments = false`,
# so a `# type: ignore` hides nothing. Checks the configured include paths, or the paths passed as arguments. Not part
# of the gate; `pyproject.toml` is unchanged.
set -euo pipefail

cd "$(dirname "$0")/.."

CONFIG="$(mktemp pyrightconfig.strict.XXXXXX.json)"
trap 'rm -f "$CONFIG"' EXIT

python3 - "$CONFIG" <<'PY'
import json
import sys
import tomllib

with open("pyproject.toml", "rb") as f:
    settings = tomllib.load(f)["tool"]["pyright"]
settings.update(typeCheckingMode="strict", enableTypeIgnoreComments=False)
with open(sys.argv[1], "w") as f:
    json.dump(settings, f)
PY

uv run pyright --project "$CONFIG" "$@"
