#!/usr/bin/env bash
# Holds the web exemplars to a gate that bundles real files: `specs` runs their specs under the hub's own
# unit-test builder, `structural-gate` runs `scripts/structural-gate.js`. Each lays the exemplar tree over a scratch copy
# of `projects/`: a file new to the tree is copied in, a file that already exists there (a key registry) has the
# exemplar's lines appended, and the hub client is regenerated with the exemplar router mounted. `fleet` resolves to the
# exemplar's barrel. The gate's layer table gains the exemplar's `permits` unit, as production's would.
set -euo pipefail

web="$(cd "$(dirname "$0")/.." && pwd)"
exemplars="$(cd "$web/../exemplars/web" && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

cp -r "$web/projects" "$web/scripts" "$web/angular.json" "$web/tsconfig.json" "$web/package.json" "$web"/vitest-*.ts "$scratch/"
ln -s "$web/node_modules" "$scratch/node_modules"
(cd "$exemplars" && find . -type f ! -path './fleet/src/lib/api/*') | while read -r file; do
  mkdir -p "$(dirname "$scratch/projects/$file")"
  if [[ -e "$scratch/projects/$file" ]]; then cat "$exemplars/$file" >> "$scratch/projects/$file"; else cp "$exemplars/$file" "$scratch/projects/$file"; fi
done
"$web/scripts/exemplar-client.sh" overlay "$scratch/projects/fleet/src/lib/api/hub"

case "${1:-}" in
  specs)
    cat > "$scratch/projects/hub/tsconfig.exemplars.spec.json" <<'JSON'
{
  "extends": "./tsconfig.spec.json",
  "compilerOptions": { "paths": { "fleet": ["../fleet/src/exemplar-api.ts"], "fleet/shell": ["../fleet/src/shell-api.ts"], "fleet/testing": ["../fleet/src/lib/testing/public-api.ts"] } }
}
JSON
    specs=()
    while read -r spec; do specs+=("--include=projects/${spec#./}"); done < <(cd "$exemplars" && find . -name '*.spec.ts')
    cd "$scratch" && npx ng test hub --watch=false --ts-config=projects/hub/tsconfig.exemplars.spec.json "${specs[@]}"
    ;;
  structural-gate)
    sed -i "s|^const HUB_LAYERS = {$|&\n  permits: [],|" "$scratch/scripts/structural-gate.js"
    grep -q "^  permits: \[\],$" "$scratch/scripts/structural-gate.js"
    cd "$scratch" && node scripts/structural-gate.js
    ;;
  *)
    echo "usage: $0 specs|structural-gate" >&2
    exit 2
    ;;
esac
