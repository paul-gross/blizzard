#!/usr/bin/env bash
# Pyright in strict mode — the report a strict-typing slice reads its before and after from.
#
# Runs the repo's `[tool.pyright]` settings with `typeCheckingMode = "strict"` and `enableTypeIgnoreComments = false`,
# so a `# type: ignore` hides nothing. Checks the configured include paths, or the paths passed as arguments. Not part
# of the gate; `pyproject.toml` is unchanged.
#
# `--shipping` narrows the report to shipping code: store migration tests are excluded (pyright applies the config's
# `exclude` to command-line paths too), and with no paths the scope is `src` and `exemplars/python`.
set -euo pipefail

cd "$(dirname "$0")/.."

SHIPPING=0
if [[ "${1:-}" == "--shipping" ]]; then
  SHIPPING=1
  shift
  if [[ $# -eq 0 ]]; then
    set -- src exemplars/python
  fi
fi

CONFIG="$(mktemp pyrightconfig.strict.XXXXXX.json)"
trap 'rm -f "$CONFIG"' EXIT

python3 - "$CONFIG" "$SHIPPING" <<'PY'
import json
import sys
import tomllib

with open("pyproject.toml", "rb") as f:
    settings = tomllib.load(f)["tool"]["pyright"]
settings.update(typeCheckingMode="strict", enableTypeIgnoreComments=False)
if sys.argv[2] == "1":
    settings["exclude"] = [*settings["exclude"], "**/store/migrations/tests"]
with open(sys.argv[1], "w") as f:
    json.dump(settings, f)
PY

uv run pyright --project "$CONFIG" "$@"
