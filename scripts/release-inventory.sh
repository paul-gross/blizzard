#!/usr/bin/env bash
# Assemble the files a release attaches into <out-dir>, then sum them.
#
# Usage: scripts/release-inventory.sh <version> <wheel> <image-digest> <out-dir> [--compat-breaks <path>]
#
#   <version>       the release version, the tag without its `v`.
#   <wheel>         the built wheel, copied byte for byte.
#   <image-digest>  the pushed multi-arch image's manifest-list digest, `sha256:<64 hex>`.
#   <out-dir>       where the attached set is written; created when absent.
#   --compat-breaks the compatibility backstop's report, copied only when the path exists.
#
# SHA256SUMS goes last, over every other file already in <out-dir> — including files
# placed there before this script runs — sorted by name, with bare filenames so
# `sha256sum -c` passes from inside <out-dir>.
set -euo pipefail

cd "$(dirname "$0")/.."
SCRIPTS="$PWD/scripts"

usage() {
  echo "usage: $0 <version> <wheel> <image-digest> <out-dir> [--compat-breaks <path>]" >&2
  exit 2
}

[ "$#" -ge 4 ] || usage
VERSION="$1"
WHEEL="$2"
IMAGE_DIGEST="$3"
OUT_DIR="$4"
shift 4

COMPAT_BREAKS=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --compat-breaks)
      [ "$#" -ge 2 ] || usage
      COMPAT_BREAKS="$2"
      shift 2
      ;;
    *) usage ;;
  esac
done

[[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || { echo "ERROR: image digest '$IMAGE_DIGEST' is not sha256:<64 hex>" >&2; exit 1; }
[ -f "$WHEEL" ] || { echo "ERROR: wheel '$WHEEL' does not exist" >&2; exit 1; }

mkdir -p "$OUT_DIR"

cp "$WHEEL" "$OUT_DIR/$(basename "$WHEEL")"
"$SCRIPTS/export-requirements.sh" "$VERSION" "$WHEEL" \
  > "$OUT_DIR/blizzard-${VERSION}-requirements.txt"
"$SCRIPTS/export-requirements.sh" "$VERSION" "$WHEEL" --extra postgres \
  > "$OUT_DIR/blizzard-${VERSION}-requirements-pg.txt"
printf 'ghcr.io/paul-gross/blizzard-hub@%s\n' "$IMAGE_DIGEST" \
  > "$OUT_DIR/blizzard-hub-${VERSION}-image.txt"

if [ -n "$COMPAT_BREAKS" ] && [ -f "$COMPAT_BREAKS" ]; then
  cp "$COMPAT_BREAKS" "$OUT_DIR/compat-breaks.json"
fi

rm -f "$OUT_DIR/SHA256SUMS"
# Captured first so SHA256SUMS never lists itself.
SUMS="$(cd "$OUT_DIR" && find . -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort | xargs sha256sum)"
printf '%s\n' "$SUMS" > "$OUT_DIR/SHA256SUMS"
