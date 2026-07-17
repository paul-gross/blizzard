#!/usr/bin/env bash
# The one build entrypoint.
#
# Turns the source tree into the single distributable wheel and proves it:
#   1. builds both Angular apps and writes their output into the wheel-embed
#      assets dir (src/blizzard/static/{hub,runner});
#   2. builds the wheel (uv build --wheel), which embeds those assets plus both
#      Alembic migration trees;
#   3. verifies the wheel actually contains the embedded frontend assets; and
#   4. installs the wheel into a clean, node-free virtualenv and runs
#      `blizzard --version`, proving the released artifact needs no Node at
#      install or runtime.
#
# Invoke as `mise run build` or `./scripts/build-wheel.sh`. Both CI (the tag-v*
# release job) and a human building locally use this same entrypoint.
#
# Optional env:
#   BLIZZARD_VERSION   override the wheel version (dev builds, tag releases).
#                      When unset the version in pyproject.toml is used as-is.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

WEB_DIR="${WEB_DIR:-web}"
HUB_ASSETS="src/blizzard/static/hub"
RUNNER_ASSETS="src/blizzard/static/runner"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# --- 1. build the two Angular apps into the wheel-embed assets dir -----------
#
# Declared interface (the P5 frontend builder owns the Angular workspace; the
# integrate step reconciles the exact paths/script names): the workspace lives
# at $WEB_DIR and exposes `npm run build`, whose configured Angular outputPaths
# write the compiled hub app into $HUB_ASSETS and the runner app into
# $RUNNER_ASSETS (see src/blizzard/static/README.md). Until that workspace lands
# the committed placeholder index.html in each dir is the embedded asset, and
# this step is a clearly-labeled no-op so the build stays green.
if [ -f "$WEB_DIR/package.json" ]; then
  log "Building both Angular apps ($WEB_DIR) into the wheel-embed assets dir"
  ( cd "$WEB_DIR" && npm ci && npm run build )
  # Fail loud if the build did not actually populate the embed dirs — a silent
  # empty static tree would ship a placeholder wheel while claiming a real one.
  for d in "$HUB_ASSETS" "$RUNNER_ASSETS"; do
    if [ ! -f "$REPO_ROOT/$d/index.html" ]; then
      echo "ERROR: '$WEB_DIR' build did not write an index.html into $d" >&2
      exit 1
    fi
  done
else
  log "PENDING (P5 frontend builder): no $WEB_DIR/package.json — shipping the committed placeholder frontend assets. Real Angular bundles embed here once the web workspace lands."
fi

# --- version override (dev builds / tag releases) ---------------------------
restore_version() { :; }
trap 'restore_version' EXIT
if [ -n "${BLIZZARD_VERSION:-}" ]; then
  log "Overriding wheel version -> $BLIZZARD_VERSION"
  cp pyproject.toml pyproject.toml.bak
  restore_version() { mv -f pyproject.toml.bak pyproject.toml 2>/dev/null || true; }
  # Rewrite only the [project] version line (the first `version = "..."`).
  python3 - "$BLIZZARD_VERSION" <<'PY'
import re, sys
v = sys.argv[1]
p = "pyproject.toml"
s = open(p).read()
s, n = re.subn(r'(?m)^version = "[^"]*"', f'version = "{v}"', s, count=1)
assert n == 1, "could not find a [project] version line to override"
open(p, "w").write(s)
PY
fi

# --- 2. build the wheel ------------------------------------------------------
log "Building the wheel (uv build --wheel)"
rm -rf dist
uv build --wheel
WHEEL="$(ls dist/blizzard-*.whl)"
log "Built $WHEEL"

# --- 3. verify the wheel contains the embedded frontend assets --------------
log "Verifying the wheel embeds the frontend assets"
python3 - "$WHEEL" <<'PY'
import sys, zipfile
wheel = sys.argv[1]
names = zipfile.ZipFile(wheel).namelist()
required = [
    "blizzard/static/hub/index.html",
    "blizzard/static/runner/index.html",
]
missing = [r for r in required if r not in names]
if missing:
    print(f"ERROR: wheel {wheel} is missing embedded assets: {missing}", file=sys.stderr)
    sys.exit(1)
# The wheel must also carry both migration trees (offline `init`/`migrate`).
migs = [n for n in names if "store/migrations/versions/" in n and n.endswith(".py") and "__init__" not in n]
if not any("hub/" in m for m in migs) or not any("runner/" in m for m in migs):
    print(f"ERROR: wheel {wheel} is missing a migration tree: {migs}", file=sys.stderr)
    sys.exit(1)
print(f"OK: wheel embeds frontend assets and both migration trees ({len(names)} members)")
PY

# --- 4. install into a clean, node-free venv and run the binary -------------
# The venv is built with the system python only; no Node is installed or on its
# PATH, proving the released artifact runs with zero Node dependency.
log "Installing the wheel into a clean virtualenv and running blizzard --version"
VENV="$(mktemp -d)/venv"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$WHEEL"
"$VENV/bin/blizzard" --version
# The daemon aliases must be installed console scripts too.
test -x "$VENV/bin/blizzard-hub"
test -x "$VENV/bin/blizzard-runner"
rm -rf "$(dirname "$VENV")"

log "Build OK: $WHEEL is a self-contained, node-free distributable."
