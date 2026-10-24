#!/usr/bin/env bash
# Assert the tag's release version agrees with pyproject.toml's [project]
# version before building. A cut commit carries Release-Cut: X.Y.Z and moves
# pyproject.toml to the most recently cut release on its branch; candidate and
# stable tags share that commit. The minor counts release trains.
#
# The tagged commit must also sit on its line: be an ancestor of origin/release/X.Y. The one
# exception is a `vX.0.0-rc.K` on origin/master while src/blizzard/wire/window.toml sets
# `opening = "X"` — a major's candidates are cut from an open master, before its line exists.
# Needs full history. LINE_REF and MASTER_REF override the two refs, as in
# check-line-provenance.sh.
#
# Usage: check-version-tag.sh <v-prefixed-tag>   (run with the tagged commit checked out)
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

major="${release_version%%.*}"
minor="${release_version#*.}"
minor="${minor%%.*}"
line="${LINE_REF:-origin/release/${major}.${minor}}"
master="${MASTER_REF:-origin/master}"

on_line() {
  git rev-parse --verify --quiet "$line^{commit}" > /dev/null && git merge-base --is-ancestor HEAD "$line"
}

opening_major_on_master() {
  [[ "$tag_version" =~ ^[0-9]+\.0\.0-rc\.[0-9]+$ ]] || return 1
  git rev-parse --verify --quiet "$master^{commit}" > /dev/null || return 1
  git merge-base --is-ancestor HEAD "$master" || return 1
  [ "$(python3 -c '
import tomllib
with open("src/blizzard/wire/window.toml", "rb") as f:
    print(tomllib.load(f).get("opening", ""))
' 2> /dev/null)" = "$major" ]
}

if ! on_line && ! opening_major_on_master; then
  echo "check-version-tag.sh: tag $tag's commit is not on its line — it must be an ancestor of $line" \
       "(fetch full history, and push the release branch first)." >&2
  exit 1
fi

echo "OK: tag $tag agrees with pyproject.toml version $pyproject_version"
