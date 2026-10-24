#!/usr/bin/env bash
# scripts/image-tags.sh — a pure function from a release tag ref to the GHCR image
# tag fan-out (issue #189). One tag (no repository prefix) per line on stdout.
#
#   v1.2.3        -> 1.2.3 / 1.2 / latest            (latest only when no listed stable tag is higher)
#   v1.2.3-rc.1   -> 1.2.3-rc.1                      (never latest, never a minor line)
#   anything else -> exit 1, nothing on stdout
#
# `image-tags.sh <tag> [<repo-tag>...]`: the trailing arguments are the repository's tags. `latest`
# goes only to the highest stable version among them and the tag itself, compared numerically per
# component, so a patch on an older line never takes it. Anything not a stable `vX.Y.Z` among them
# is ignored; with none, the tag is vacuously the highest.
#
# Pulled out of workflow YAML so the logic deciding whether `latest` moves is
# unit-testable (tests/test_image_tags.py) instead of buried in
# .github/workflows/release.yml. This script decides *what* to tag; the workflow
# still owns the registry/repository prefix (ghcr.io/paul-gross/blizzard-hub).
set -euo pipefail

tag="${1:?usage: image-tags.sh <v-prefixed-tag> [<repo-tag>...]}"
shift

case "$tag" in
  v*) ;;
  *)
    echo "image-tags.sh: not a v-prefixed tag: $tag" >&2
    exit 1
    ;;
esac

version="${tag#v}"

case "$version" in
  *-rc.*)
    # Prerelease: its exact version only — never latest, never a minor line.
    echo "$version"
    ;;
  [0-9]*.[0-9]*.[0-9]*)
    if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      echo "image-tags.sh: not a supported stable version shape: $version" >&2
      exit 1
    fi
    major_minor="${version%.*}"
    echo "$version"
    echo "$major_minor"
    highest=true
    for other in "$@"; do
      [[ "$other" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] || continue
      IFS=. read -r a b c <<< "$version"
      if [ "${BASH_REMATCH[1]}" -gt "$a" ] ||
        { [ "${BASH_REMATCH[1]}" -eq "$a" ] && [ "${BASH_REMATCH[2]}" -gt "$b" ]; } ||
        { [ "${BASH_REMATCH[1]}" -eq "$a" ] && [ "${BASH_REMATCH[2]}" -eq "$b" ] && [ "${BASH_REMATCH[3]}" -gt "$c" ]; }; then
        highest=false
      fi
    done
    if [ "$highest" = true ]; then
      echo "latest"
    fi
    ;;
  *)
    echo "image-tags.sh: not a supported version shape: $version" >&2
    exit 1
    ;;
esac
