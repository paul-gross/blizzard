#!/usr/bin/env bash
# scripts/release-cut-in-range.sh — is a pull request a release cut?
#
# Usage: release-cut-in-range.sh <base> <head>
#
# Prints `true` when any commit in <base>..<head> carries a `Release-Cut: <X.Y.Z>` trailer, and
# `false` otherwise. pr.yml reads it to decide whether the upgrade rehearsal runs.
# Pulled out of workflow YAML so it is unit-testable (tests/test_release_cut_in_range.py).
set -euo pipefail

base="${1:?usage: release-cut-in-range.sh <base> <head>}"
head="${2:?usage: release-cut-in-range.sh <base> <head>}"

# shellcheck source=scripts/trailers.sh
source "$(dirname "$0")/trailers.sh"

while read -r commit; do
  [ -n "$commit" ] || continue
  if has_trailer 'Release-Cut' "$(git log -1 --format=%B "$commit")"; then
    echo true
    exit 0
  fi
done < <(git rev-list "$base..$head")

echo false
