#!/usr/bin/env bash
# Export uv.lock as a hashed requirements file on stdout — the dependency set the
# gate proved, which an image build, the build's install proof, and a release all
# install from.
#
# Usage: scripts/export-requirements.sh <version> <wheel> [--extra <name>] [--no-project]
#
#   <version>      the release or dev version the wheel is published under; names
#                  the release address in the project line (v<version>).
#   <wheel>        the built wheel; its filename and sha256 form the project line.
#   --extra <name> include that optional dependency group (the image uses postgres).
#   --no-project   leave the project line off, for an install that takes the wheel
#                  from its own context.
#
# --locked makes the export fail when uv.lock disagrees with pyproject.toml, so a
# build never installs a set the gate did not run. That includes a pyproject.toml
# whose version build-wheel.sh has overridden: export after it restores the file.
set -euo pipefail

cd "$(dirname "$0")/.."

usage() { echo "usage: $0 <version> <wheel> [--extra <name>] [--no-project]" >&2; exit 2; }

[ "$#" -ge 2 ] || usage
VERSION="$1"
WHEEL="$2"
shift 2

EXTRAS=()
EMIT_PROJECT=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --extra)
      [ "$#" -ge 2 ] || usage
      EXTRAS+=(--extra "$2")
      shift 2
      ;;
    --no-project)
      EMIT_PROJECT=0
      shift
      ;;
    *) usage ;;
  esac
done

[ -f "$WHEEL" ] || { echo "ERROR: wheel '$WHEEL' does not exist" >&2; exit 1; }

# Capture first so a failed export (a stale lock) emits nothing, not a truncated file.
REQUIREMENTS="$(uv export --locked --no-dev --no-emit-project --format requirements-txt "${EXTRAS[@]}")"
printf '%s\n' "$REQUIREMENTS"

if [ "$EMIT_PROJECT" -eq 1 ]; then
  DIGEST="$(sha256sum "$WHEEL" | cut -d' ' -f1)"
  printf 'blizzard @ https://github.com/paul-gross/blizzard/releases/download/v%s/%s --hash=sha256:%s\n' \
    "$VERSION" "$(basename "$WHEEL")" "$DIGEST"
fi
