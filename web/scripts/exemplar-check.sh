#!/usr/bin/env bash
# The web exemplars' gate (blizzard:exemplar-check), run by `mise run exemplar-check` and CI's `exemplars` job alike:
# a strict-template type-check against the real hub app, the hub's lint config, the client stand-in's drift, the
# specs, and the structural gate. The client steps export the exemplar router's spec, so they need `uv sync` too.
set -euo pipefail

web="$(cd "$(dirname "$0")/.." && pwd)"

(cd "$web" && npx ngc -p tsconfig.exemplars.json)
(cd "$web/../exemplars/web" && "$web/node_modules/.bin/eslint" --max-warnings=0 -c "$web/eslint.exemplars.config.js" .)
"$web/scripts/exemplar-client.sh" check
"$web/scripts/exemplar-tree.sh" specs
"$web/scripts/exemplar-tree.sh" structural-gate
