#!/usr/bin/env bash
# scripts/check-line-provenance.sh — hold a pull request into a release line to where
# its commits came from.
#
# Usage: check-line-provenance.sh <base> <head>
#
# Every commit in <base>..<head> must be one of:
#   - a cherry-pick: a `(cherry picked from commit <sha>)` line naming a commit that is an
#     ancestor of origin/master (a commit with several such lines passes if any qualifies);
#   - line-only: a `Line-only: <reason>` trailer;
#   - a cut: a `Release-Cut: <X.Y.Z>` trailer.
# Merge commits are classified like any other. Prints each commit that is none of them and
# exits 1; the trailers are read as git trailers, not free text.
# Pulled out of workflow YAML so it is unit-testable (tests/test_check_line_provenance.py).
set -euo pipefail

base="${1:?usage: check-line-provenance.sh <base> <head>}"
head="${2:?usage: check-line-provenance.sh <base> <head>}"
master="${MASTER_REF:-origin/master}"

git rev-parse --verify --quiet "$master^{commit}" > /dev/null ||
  { echo "check-line-provenance.sh: $master is not available; fetch full history" >&2; exit 2; }

# shellcheck source=scripts/trailers.sh
source "$(dirname "$0")/trailers.sh"

has_master_cherry_pick() {
  local sha
  while read -r sha; do
    if git cat-file -e "$sha^{commit}" 2> /dev/null && git merge-base --is-ancestor "$sha" "$master"; then
      return 0
    fi
  done < <(grep -Eo '\(cherry picked from commit [0-9a-f]{7,64}\)' <<< "$1" | grep -Eo '[0-9a-f]{7,64}' || true)
  return 1
}

failed=0
while read -r commit; do
  [ -n "$commit" ] || continue
  message="$(git log -1 --format=%B "$commit")"
  if has_trailer 'Line-only' "$message" || has_trailer 'Release-Cut' "$message" || has_master_cherry_pick "$message"; then
    continue
  fi
  echo "$(git log -1 --format='%h %s' "$commit") — not a cherry-pick of a master commit, a Line-only: commit, or a Release-Cut: commit" >&2
  failed=1
done < <(git rev-list "$base..$head")

exit "$failed"
