#!/usr/bin/env bash
# One shard of a weighted Playwright pool: list the suite with the given project flags, plan this shard's
# (project, spec file) units by tests/shard-weights.json, and run exactly those with --test-list — never --shard.
#
# Usage: scripts/playwright-shard.sh --shard=<index>/<count> [playwright test args, e.g. --project=…]
# A shard the plan leaves empty exits 0 without invoking Playwright: an empty --test-list must never become an
# unfiltered run. The setup/teardown projects still run with the selected projects that depend on them.
set -euo pipefail

cd "$(dirname "$0")/.."

shard=""
args=()
for arg in "$@"; do
  case "$arg" in
    --shard=*) shard="${arg#--shard=}" ;;
    *) args+=("$arg") ;;
  esac
done
if [ -z "$shard" ]; then
  echo "playwright-shard: --shard=<index>/<count> is required" >&2
  exit 2
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

(cd tests && npx playwright test "${args[@]}" --list --reporter=json) > "$work/list.json"
uv run python -m tests.support.shard_weights plan-playwright --shard "$shard" --list "$work/list.json" > "$work/test-list.txt"

if [ ! -s "$work/test-list.txt" ]; then
  echo "playwright-shard: shard $shard owns no unit — nothing to run"
  exit 0
fi
echo "playwright-shard: shard $shard runs these --test-list lines:"
cat "$work/test-list.txt"
cd tests && npx playwright test "${args[@]}" --test-list "$work/test-list.txt"
