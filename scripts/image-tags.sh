#!/usr/bin/env bash
# scripts/image-tags.sh — a pure function from a release tag ref to the GHCR image
# tag fan-out (issue #189). One tag (no repository prefix) per line on stdout.
#
#   v1.2.3        -> 1.2.3 / 1.2 / latest
#   v1.2.3-rc.1   -> 1.2.3-rc.1                      (never latest, never a minor line)
#   anything else -> exit 1, nothing on stdout
#
# Pulled out of workflow YAML so the logic deciding whether `latest` moves is
# unit-testable (tests/test_image_tags.py) instead of buried in
# .github/workflows/release.yml. This script decides *what* to tag; the workflow
# still owns the registry/repository prefix (ghcr.io/paul-gross/blizzard-hub).
set -euo pipefail

tag="${1:?usage: image-tags.sh <v-prefixed-tag>}"

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
    echo "latest"
    ;;
  *)
    echo "image-tags.sh: not a supported version shape: $version" >&2
    exit 1
    ;;
esac
