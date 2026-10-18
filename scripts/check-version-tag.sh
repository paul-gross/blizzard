#!/usr/bin/env bash
# Assert the tag's release version agrees with pyproject.toml's [project]
# version before building. A cut commit carries Release-Cut: X.Y.Z and moves
# pyproject.toml to the most recently cut release on its branch; candidate and
# stable tags share that commit. The minor counts release trains.
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
release_version="${tag_version%-rc.*}"

pyproject_version="$(python3 -c '
import tomllib
with open("pyproject.toml", "rb") as f:
    print(tomllib.load(f)["project"]["version"])
')"

if [ "$release_version" != "$pyproject_version" ]; then
  echo "check-version-tag.sh: tag $tag names version '$tag_version' (release '$release_version')," \
       "but pyproject.toml's [project] version is '$pyproject_version' — set it in the cut commit." >&2
  exit 1
fi

echo "OK: tag $tag agrees with pyproject.toml version $pyproject_version"
