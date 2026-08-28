#!/usr/bin/env bash
# scripts/is-retryable-push-error.sh — a pure predicate over a GHCR push's
# captured output, deciding whether it names a transient error: a 403 secondary
# rate limit, a 429, or a 5xx. Pulled out of the dev-image job's
# retry loop precisely so the classification is unit-testable
# (tests/test_is_retryable_push_error.py) instead of buried in workflow YAML.
#
# Usage: is-retryable-push-error.sh [logfile]   (reads stdin if omitted)
# Exit 0: retryable. Exit 1: not — a genuine auth or manifest error, which
# must fail the job on the spot rather than burn the remaining attempts.
set -euo pipefail

log="$(cat "${1:-/dev/stdin}")"

shopt -s nocasematch
if [[ "$log" =~ secondary\ rate\ limit ]]; then
  exit 0
fi
if [[ "$log" =~ (429\ Too\ Many\ Requests|http\ status\ code\ 429) ]]; then
  exit 0
fi
if [[ "$log" =~ http\ status\ code\ 5[0-9][0-9] ]]; then
  exit 0
fi
exit 1
