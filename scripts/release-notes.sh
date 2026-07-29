#!/usr/bin/env bash
# scripts/release-notes.sh — generate a release-notes skeleton grouped by
# Conventional Commit type since the previous tag (issue #190). Two modes:
#
#   release-notes.sh --range <from>..<to>   real git log over the given range
#   release-notes.sh --from-stdin           read "<sha> <subject>" lines from
#                                            stdin — what tests/test_release_notes.py
#                                            drives, over a synthetic commit list
#                                            with no real git history needed
#
# A `!` before the colon (`feat!: ...`, `feat(scope)!: ...`) is this project's
# breaking-change marker (docs/versioning.md) and surfaces at the top. A
# placeholder **Upgrade notes** heading always leads the notes — hand-write it
# whenever the release asks something of the operator.
set -euo pipefail

_TYPE_ORDER=(feat fix perf docs refactor test chore style ai)
declare -A _TYPE_TITLES=(
  [feat]="Features"
  [fix]="Fixes"
  [perf]="Performance"
  [docs]="Documentation"
  [refactor]="Refactoring"
  [test]="Tests"
  [chore]="Chores"
  [style]="Style"
  [ai]="AI-assisted"
)

generate_from_lines() {
  local breaking=()
  local other=()
  declare -A grouped

  while IFS= read -r line; do
    [ -z "$line" ] && continue
    sha="${line%% *}"
    subject="${line#* }"
    # Conventional Commit: type(scope)?!?: description
    if [[ "$subject" =~ ^([a-zA-Z]+)(\([^\)]*\))?(\!)?:\ (.*)$ ]]; then
      type="${BASH_REMATCH[1]}"
      bang="${BASH_REMATCH[3]}"
      desc="${BASH_REMATCH[4]}"
      entry="- ${desc} (${sha})"
      if [ -n "$bang" ]; then
        breaking+=("$entry")
      fi
      grouped["$type"]="${grouped[$type]:-}${entry}"$'\n'
    else
      other+=("- ${subject} (${sha})")
    fi
  done

  if [ "${#breaking[@]}" -gt 0 ]; then
    echo "## Breaking changes"
    echo
    for e in "${breaking[@]}"; do echo "$e"; done
    echo
  fi

  echo "## Upgrade notes"
  echo
  echo "_Fill in when this release asks something of the operator — see docs/versioning.md._"
  echo

  for type in "${_TYPE_ORDER[@]}"; do
    if [ -n "${grouped[$type]:-}" ]; then
      echo "## ${_TYPE_TITLES[$type]}"
      echo
      printf '%s' "${grouped[$type]}"
      echo
    fi
  done

  if [ "${#other[@]}" -gt 0 ]; then
    echo "## Other"
    echo
    for e in "${other[@]}"; do echo "$e"; done
    echo
  fi
}

case "${1:-}" in
  --from-stdin)
    generate_from_lines
    ;;
  --range)
    range="${2:?usage: release-notes.sh --range <from>..<to>}"
    git log --format='%h %s' "$range" | generate_from_lines
    ;;
  *)
    echo "usage: release-notes.sh --range <from>..<to> | --from-stdin" >&2
    exit 1
    ;;
esac
