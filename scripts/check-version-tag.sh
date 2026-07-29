#!/usr/bin/env bash
# scripts/check-version-tag.sh — assert pyproject.toml's [project] version agrees
# with a release tag (issue #190). Wired as an early failing step in
# .github/workflows/release.yml's release job, so a version/tag mismatch fails
# before any wheel or image is built — the version bump is part of the release
# ceremony (bzh:release step 3), not something this script does for you.
#
# Usage: check-version-tag.sh <v-prefixed-tag>
set -euo pipefail

cd "$(dirname "$0")/.."

tag="${1:?usage: check-version-tag.sh <v-prefixed-tag>}"
case "$tag" in
  v*) ;;
  *)
    echo "check-version-tag.sh: not a v-prefixed tag: $tag" >&2
    exit 1
    ;;
esac
tag_version="${tag#v}"

pyproject_version="$(python3 -c '
import tomllib
with open("pyproject.toml", "rb") as f:
    print(tomllib.load(f)["project"]["version"])
')"

if [ "$tag_version" != "$pyproject_version" ]; then
  echo "check-version-tag.sh: tag $tag names version '$tag_version', but pyproject.toml's" \
       "[project] version is '$pyproject_version' — bump pyproject.toml in the release-prep" \
       "commit before tagging (bzh:release step 3)." >&2
  exit 1
fi

echo "OK: tag $tag agrees with pyproject.toml version $pyproject_version"
