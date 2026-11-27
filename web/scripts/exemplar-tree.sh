#!/usr/bin/env bash
# Holds the web exemplars to a gate that bundles real files: `specs` runs their specs under the hub's own
# unit-test builder, `structural-gate` runs `scripts/structural-gate.js`. Each lays the exemplar tree over a scratch copy
# of `projects/`: a file new to the tree is copied in, a file that already exists there (a key registry) has the
# exemplar's lines appended, and the hub client is regenerated with the exemplar router mounted. `fleet/hub-api` resolves to the
# exemplar's entry. The gate's layer table gains the exemplar's `permits`, `cadence-edit` and `proposal-acceptance`
# units, as production's would. Each unit reads wire types only behind the domain boundary.
set -euo pipefail

web="$(cd "$(dirname "$0")/.." && pwd)"
exemplars="$(cd "$web/../exemplars/web" && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

cp -r "$web/projects" "$web/scripts" "$web/angular.json" "$web/tsconfig.json" "$web/package.json" "$web"/vitest-*.ts "$web"/vitest-*.mts "$scratch/"
ln -s "$web/node_modules" "$scratch/node_modules"
(cd "$exemplars" && find . -type f ! -path './fleet/src/lib/api/*') | while read -r file; do
  mkdir -p "$(dirname "$scratch/projects/$file")"
  if [[ -e "$scratch/projects/$file" ]]; then cat "$exemplars/$file" >> "$scratch/projects/$file"; else cp "$exemplars/$file" "$scratch/projects/$file"; fi
done
"$web/scripts/exemplar-client.sh" overlay "$scratch/projects/fleet/src/lib/api/hub"
# The typed stubs key on the operation tables, so they follow the overlaid client.
node "$web/scripts/generate-stub-operations.mjs" "$scratch/projects"

case "${1:-}" in
  specs)
    cat > "$scratch/projects/hub/tsconfig.exemplars.spec.json" <<'JSON'
{
  "extends": "./tsconfig.spec.json",
  "compilerOptions": {
    "paths": {
      "fleet/awaiting-human": ["../fleet/src/entries/awaiting-human.ts"],
      "fleet/chrome": ["../fleet/src/entries/chrome.ts"],
      "fleet/chrome/titlebar": ["../fleet/src/entries/chrome/titlebar.ts"],
      "fleet/chunk-card": ["../fleet/src/entries/chunk-card.ts"],
      "fleet/chunk-detail": ["../fleet/src/entries/chunk-detail.ts"],
      "fleet/chunk-detail/actions": ["../fleet/src/entries/chunk-detail/actions.ts"],
      "fleet/chunk-detail/panes": ["../fleet/src/entries/chunk-detail/panes.ts"],
      "fleet/chunk-page": ["../fleet/src/entries/chunk-page.ts"],
      "fleet/chunk-page/view": ["../fleet/src/entries/chunk-page/view.ts"],
      "fleet/core": ["../fleet/src/entries/core.ts"],
      "fleet/forms": ["../fleet/src/entries/forms.ts"],
      "fleet/host": ["../fleet/src/entries/host.ts"],
      "fleet/hub-api": ["../fleet/src/exemplar-hub-api.ts"],
      "fleet/kit": ["../fleet/src/entries/kit.ts"],
      "fleet/kit/accordion": ["../fleet/src/entries/kit/accordion.ts"],
      "fleet/kit/chips": ["../fleet/src/entries/kit/chips.ts"],
      "fleet/kit/combobox": ["../fleet/src/entries/kit/combobox.ts"],
      "fleet/kit/dialog": ["../fleet/src/entries/kit/dialog.ts"],
      "fleet/kit/grid": ["../fleet/src/entries/kit/grid.ts"],
      "fleet/kit/menu": ["../fleet/src/entries/kit/menu.ts"],
      "fleet/kit/select": ["../fleet/src/entries/kit/select.ts"],
      "fleet/kit/select-list": ["../fleet/src/entries/kit/select-list.ts"],
      "fleet/kit/tabs": ["../fleet/src/entries/kit/tabs.ts"],
      "fleet/kit/tooltip": ["../fleet/src/entries/kit/tooltip.ts"],
      "fleet/runner-api": ["../fleet/src/entries/runner-api.ts"],
      "fleet/sse": ["../fleet/src/entries/sse.ts"],
      "fleet/transcripts": ["../fleet/src/entries/transcripts.ts"],
      "fleet/transcripts/tab": ["../fleet/src/entries/transcripts/tab.ts"],
      "fleet/viewport": ["../fleet/src/entries/viewport.ts"],
      "fleet/viewport/menu": ["../fleet/src/entries/viewport/menu.ts"],
      "fleet/shell": ["../fleet/src/shell-api.ts"],
      "fleet/runner-shell": ["../fleet/src/runner-shell-api.ts"],
      "fleet/testing": ["../fleet/src/lib/testing/public-api.ts"]
    }
  }
}
JSON
    specs=()
    while read -r spec; do specs+=("--include=projects/${spec#./}"); done < <(cd "$exemplars" && find . -name '*.spec.ts')
    cd "$scratch" && npx ng test hub --watch=false --ts-config=projects/hub/tsconfig.exemplars.spec.json "${specs[@]}"
    ;;
  types)
    # The strict-template type-check over the overlay, so an exemplar's routes are typed against its own operation table.
    files=()
    while read -r file; do files+=("\"./projects/${file#./}\""); done < <(cd "$exemplars" && find . -name '*.ts' ! -path './fleet/src/lib/api/*')
    cat > "$scratch/tsconfig.exemplars.json" <<JSON
{
  "extends": "./tsconfig.json",
  "compilerOptions": {
    "noEmit": true,
    "types": ["vitest/globals"],
    "paths": {
      "fleet/awaiting-human": ["./projects/fleet/src/entries/awaiting-human.ts"],
      "fleet/chrome": ["./projects/fleet/src/entries/chrome.ts"],
      "fleet/chrome/titlebar": ["./projects/fleet/src/entries/chrome/titlebar.ts"],
      "fleet/chunk-card": ["./projects/fleet/src/entries/chunk-card.ts"],
      "fleet/chunk-detail": ["./projects/fleet/src/entries/chunk-detail.ts"],
      "fleet/chunk-detail/actions": ["./projects/fleet/src/entries/chunk-detail/actions.ts"],
      "fleet/chunk-detail/panes": ["./projects/fleet/src/entries/chunk-detail/panes.ts"],
      "fleet/chunk-page": ["./projects/fleet/src/entries/chunk-page.ts"],
      "fleet/chunk-page/view": ["./projects/fleet/src/entries/chunk-page/view.ts"],
      "fleet/core": ["./projects/fleet/src/entries/core.ts"],
      "fleet/forms": ["./projects/fleet/src/entries/forms.ts"],
      "fleet/host": ["./projects/fleet/src/entries/host.ts"],
      "fleet/hub-api": ["./projects/fleet/src/exemplar-hub-api.ts"],
      "fleet/kit": ["./projects/fleet/src/entries/kit.ts"],
      "fleet/kit/accordion": ["./projects/fleet/src/entries/kit/accordion.ts"],
      "fleet/kit/chips": ["./projects/fleet/src/entries/kit/chips.ts"],
      "fleet/kit/combobox": ["./projects/fleet/src/entries/kit/combobox.ts"],
      "fleet/kit/dialog": ["./projects/fleet/src/entries/kit/dialog.ts"],
      "fleet/kit/grid": ["./projects/fleet/src/entries/kit/grid.ts"],
      "fleet/kit/menu": ["./projects/fleet/src/entries/kit/menu.ts"],
      "fleet/kit/select": ["./projects/fleet/src/entries/kit/select.ts"],
      "fleet/kit/select-list": ["./projects/fleet/src/entries/kit/select-list.ts"],
      "fleet/kit/tabs": ["./projects/fleet/src/entries/kit/tabs.ts"],
      "fleet/kit/tooltip": ["./projects/fleet/src/entries/kit/tooltip.ts"],
      "fleet/runner-api": ["./projects/fleet/src/entries/runner-api.ts"],
      "fleet/sse": ["./projects/fleet/src/entries/sse.ts"],
      "fleet/transcripts": ["./projects/fleet/src/entries/transcripts.ts"],
      "fleet/transcripts/tab": ["./projects/fleet/src/entries/transcripts/tab.ts"],
      "fleet/viewport": ["./projects/fleet/src/entries/viewport.ts"],
      "fleet/viewport/menu": ["./projects/fleet/src/entries/viewport/menu.ts"],
      "fleet/shell": ["./projects/fleet/src/shell-api.ts"],
      "fleet/runner-shell": ["./projects/fleet/src/runner-shell-api.ts"],
      "fleet/testing": ["./projects/fleet/src/lib/testing/public-api.ts"],
      "@angular/*/testing": ["./node_modules/@angular/*/types/testing.d.ts"],
      "*": ["./node_modules/*"]
    }
  },
  "files": [],
  "include": [$(IFS=,; echo "${files[*]}")]
}
JSON
    cd "$scratch" && npx ngc -p tsconfig.exemplars.json
    ;;
  structural-gate)
    sed -i "s|^const HUB_LAYERS = {$|&\n  permits: [],|" "$scratch/scripts/structural-gate.js"
    grep -q "^  permits: \[\],$" "$scratch/scripts/structural-gate.js"
    sed -i "s|^const HUB_LAYERS = {$|&\n  'cadence-edit': [],|" "$scratch/scripts/structural-gate.js"
    grep -q "^  'cadence-edit': \[\],$" "$scratch/scripts/structural-gate.js"
    sed -i "s|^const HUB_LAYERS = {$|&\n  'proposal-acceptance': [],|" "$scratch/scripts/structural-gate.js"
    grep -q "^  'proposal-acceptance': \[\],$" "$scratch/scripts/structural-gate.js"
    cd "$scratch" && node scripts/structural-gate.js
    ;;
  *)
    echo "usage: $0 types|specs|structural-gate" >&2
    exit 2
    ;;
esac
