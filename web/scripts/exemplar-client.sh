#!/usr/bin/env bash
# Generates the exemplar hub client as `npm run generate:client` generates the real one: the exemplar
# router's OpenAPI spec, run through openapi-ts with the hub's plugins. `check` (the default) fails when the committed
# stand-in differs from it, as the client-drift check does; `write` refreshes the stand-in; `overlay <dir>` generates the
# hub spec with the exemplar router mounted into `<dir>`, the client a spec build bundles.
set -euo pipefail

web="$(cd "$(dirname "$0")/.." && pwd)"
python="$(cd "$web/../exemplars/python" && pwd)"
standin="$(cd "$web/../exemplars/web/fleet/src/lib/api/hub" && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

# Writes the spec to $1; with $2 = hub, the exemplar router is mounted on the real hub app.
spec() {
  (cd "$web/.." && PYTHONPATH="$python" uv run python - "$1" "${2:-}" >/dev/null <<'PY')
import json, sys
from pathlib import Path

from fastapi import FastAPI

from blizzard import __version__
from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.hub.app import create_app_for_export
from exemplar.hub.api.permits import router

app = create_app_for_export(packaged_flag_registry()) if sys.argv[2] == "hub" else FastAPI(title="blizzard-hub", version=__version__)
app.include_router(router)
Path(sys.argv[1]).write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
PY
}

generate() {
  (cd "$web" && EXEMPLAR_OPENAPI_INPUT="$1" EXEMPLAR_OPENAPI_OUTPUT="$2" npx openapi-ts -f openapi-ts.exemplars.config.ts >/dev/null)
}

case "${1:-check}" in
  check)
    spec "$scratch/spec.json"
    generate "$scratch/spec.json" "$scratch/out"
    status=0
    for file in sdk.gen.ts types.gen.ts; do diff -u "$standin/$file" "$scratch/out/$file" || status=1; done
    extra="$(cd "$standin" && find . -type f ! -name sdk.gen.ts ! -name types.gen.ts)"
    if [[ -n "$extra" ]]; then echo "not generated: $extra" >&2; status=1; fi
    if [[ $status -ne 0 ]]; then echo "exemplar client drifted — run web/scripts/exemplar-client.sh write" >&2; fi
    exit $status
    ;;
  write)
    spec "$scratch/spec.json"
    generate "$scratch/spec.json" "$scratch/out"
    cp "$scratch/out/sdk.gen.ts" "$scratch/out/types.gen.ts" "$standin/"
    ;;
  overlay)
    spec "$scratch/spec.json" hub
    generate "$scratch/spec.json" "$2"
    ;;
esac
