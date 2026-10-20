#!/usr/bin/env bash
# The upgrade rehearsal (blizzard:rehearsal). With no BLIZZARD_REHEARSAL_CANDIDATE, builds HEAD's wheel and its Postgres
# requirements, and stages them outside the checkout, where the tier's repo-read guard lets the tests read them.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -z "${BLIZZARD_REHEARSAL_CANDIDATE:-}" ]; then
  ./scripts/build-wheel.sh
  candidate="$(mktemp -d)"
  trap 'rm -rf "$candidate"' EXIT
  cp dist/blizzard-*.whl "$candidate"
  ./scripts/export-requirements.sh candidate "$(ls dist/blizzard-*.whl)" --extra postgres --no-project \
    > "$candidate/blizzard-candidate-requirements-pg.txt"
  export BLIZZARD_REHEARSAL_CANDIDATE="$candidate"
fi

BLIZZARD_REHEARSAL=1 uv run --extra postgres pytest -m rehearsal tests/rehearsal/
