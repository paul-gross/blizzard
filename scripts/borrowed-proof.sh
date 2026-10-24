#!/usr/bin/env bash
# scripts/borrowed-proof.sh — decide whether a stable tag borrows its candidate's proof.
#
# Usage: borrowed-proof.sh <v-prefixed-tag> <commit-sha>
#
# Prints `borrowed=true` or `borrowed=false` on stdout (a line for $GITHUB_OUTPUT) and exits:
#   0 — decided. A stable tag borrows when its commit carries a successful `release` run of a
#       `vX.Y.Z-rc.K` tag of the same version; a candidate tag never borrows; a patch `vX.Y.P`
#       (P >= 1) without one runs the full suite, as a patch has no candidate to borrow from.
#   1 — refused: a promotion `vX.Y.0` whose commit carries no green candidate run.
# Needs `gh` and GH_TOKEN with `actions: read`; GH_REPO names the repository (default: the
# workflow's GITHUB_REPOSITORY). Pulled out of workflow YAML so it is unit-testable
# (tests/test_borrowed_proof.py).
set -euo pipefail

tag="${1:?usage: borrowed-proof.sh <v-prefixed-tag> <commit-sha>}"
sha="${2:?usage: borrowed-proof.sh <v-prefixed-tag> <commit-sha>}"
repo="${GH_REPO:-${GITHUB_REPOSITORY:?GH_REPO or GITHUB_REPOSITORY is required}}"

case "$tag" in
  v*-rc.*)
    echo "borrowed=false"
    exit 0
    ;;
  v[0-9]*.[0-9]*.[0-9]*) ;;
  *)
    echo "borrowed-proof.sh: not a v-prefixed release tag: $tag" >&2
    exit 1
    ;;
esac

version="${tag#v}"

candidates="$(gh api --paginate \
  "repos/${repo}/actions/workflows/release.yml/runs?head_sha=${sha}&event=push&status=success&per_page=100" \
  --jq '.workflow_runs[].head_branch')"

if grep -Eq "^v${version//./\\.}-rc\.[0-9]+$" <<< "$candidates"; then
  echo "borrowed=true"
  exit 0
fi

case "$version" in
  *.0)
    echo "borrowed-proof.sh: $tag is a promotion, and $sha carries no green v${version}-rc.K release run to borrow" \
      "— cut and prove a candidate on this commit first." >&2
    exit 1
    ;;
esac

echo "borrowed=false"
