#!/usr/bin/env bash
# Resolve the blizzard-mock ref a run proves against, and the commit it names.
#
#   a `baseline` release tag                -> the mock's tag of that name, else `master`
#   a `v*` tag                              -> the mock's same-named tag (must exist)
#   pull request into `release/X.Y`         -> the head branch on the mock, else `release/X.Y` (must exist)
#   pull request into, or push to, master   -> the head branch on the mock, else `master`
#   anything else                           -> fails; a run is never defaulted to `master`
#
# Reads the run's context from the environment — BASELINE, EVENT_NAME, REF (github.ref),
# BASE_REF, HEAD_REF — and the mock's remote from MOCK_URL, so tests point it at a scratch
# repository. Prints `ref=<ref>` and `sha=<commit>` on stdout; a tag resolves to the
# commit it peels to. The decision lives here rather than in workflow YAML so it is
# unit-testable (tests/test_resolve_mock_ref.py).
set -euo pipefail

baseline="${BASELINE:-}"
event="${EVENT_NAME:-}"
ref="${REF:-}"
base="${BASE_REF:-}"
head="${HEAD_REF:-}"
url="${MOCK_URL:?MOCK_URL is required}"

fail() {
  echo "::error::resolve-mock-ref: $*" >&2
  exit 1
}

# The commit a branch tip names on the mock, or nothing if the branch is absent.
branch_sha() {
  git ls-remote --heads "$url" "refs/heads/$1" | awk -v want="refs/heads/$1" '$2 == want { print $1 }'
}

# The commit a tag peels to on the mock, or nothing if the tag is absent.
tag_sha() {
  local out
  out="$(git ls-remote --tags "$url" "refs/tags/$1" "refs/tags/$1^{}")"
  awk -v peeled="refs/tags/$1^{}" '$2 == peeled { print $1 }' <<< "$out" | grep . ||
    awk -v want="refs/tags/$1" '$2 == want { print $1 }' <<< "$out"
}

emit() {
  echo "ref=$1"
  echo "sha=$2"
}

# The head branch if the mock carries one of that name, else the line's own branch.
branch_or_fallback() {
  local fallback="$1" sha
  if [ -n "$head" ] && sha="$(branch_sha "$head")" && [ -n "$sha" ]; then
    emit "$head" "$sha"
    return
  fi
  sha="$(branch_sha "$fallback")"
  [ -n "$sha" ] || fail "blizzard-mock has no branch '$fallback' to prove this run against"
  emit "$fallback" "$sha"
}

# A rehearsal leg drives the release it seeds with that release's own mock where one was tagged.
if [ -n "$baseline" ]; then
  sha="$(tag_sha "$baseline" || true)"
  if [ -n "$sha" ]; then
    emit "$baseline" "$sha"
  else
    sha="$(branch_sha master)"
    [ -n "$sha" ] || fail "blizzard-mock has no branch 'master' to rehearse '$baseline' against"
    emit master "$sha"
  fi
  exit 0
fi

case "$ref" in
  refs/tags/v*)
    tag="${ref#refs/tags/}"
    sha="$(tag_sha "$tag" || true)"
    [ -n "$sha" ] || fail "blizzard-mock has no tag '$tag'; push the mock's tag of the same name first"
    emit "$tag" "$sha"
    exit 0
    ;;
esac

case "$event" in
  pull_request | pull_request_target)
    case "$base" in
      master) branch_or_fallback master ;;
      release/*) branch_or_fallback "$base" ;;
      *) fail "a pull request into '$base' matches no mock-ref rule" ;;
    esac
    ;;
  push)
    [ "$ref" = refs/heads/master ] || fail "a push to '$ref' matches no mock-ref rule"
    branch_or_fallback master
    ;;
  *) fail "event '$event' on '$ref' matches no mock-ref rule" ;;
esac
