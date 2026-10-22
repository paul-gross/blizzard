#!/usr/bin/env bash
# blizzard:gate-full — the local mirror of the pull-request pipeline (.github/workflows/pr.yml).
#
# Runs the merge gate (scripts/ci-gate.sh), then every upper tier pr.yml runs as its own job:
# service, the FULL crash sweep, e2e, journey, image-smoke, compose-smoke. The tiers run
# serially, since the smokes bind fixed ports. A red tier does not stop the later ones; the run
# ends with a per-tier verdict and exits non-zero if any tier was red. The release-cut upgrade
# rehearsal is not part of it (it needs `gh` and a Postgres): `mise run rehearsal` runs it.
#
# Invoke as `mise run gate-full` or `./scripts/gate-full.sh`. GATE_FULL_TIERS (space-separated
# tier names) narrows the run, e.g. `GATE_FULL_TIERS="journey service-test"`.
set -uo pipefail

cd "$(dirname "$0")/.."

tiers=(gate service-test crash-sweep e2e journey image-smoke compose-smoke)
run_gate() { ./scripts/ci-gate.sh; }

if [ -n "${GATE_FULL_TIERS:-}" ]; then
  read -r -a tiers <<< "$GATE_FULL_TIERS"
fi

verdicts=()
failed=0
for tier in "${tiers[@]}"; do
  printf '\n\033[1;36m==> gate-full: %s\033[0m\n' "$tier"
  start=$SECONDS
  if [ "$tier" = gate ]; then run_gate; else mise run "$tier"; fi
  status=$?
  seconds=$((SECONDS - start))
  if [ "$status" -eq 0 ]; then
    verdicts+=("$(printf 'PASS  %-14s %ss' "$tier" "$seconds")")
  else
    verdicts+=("$(printf 'FAIL  %-14s %ss (exit %s)' "$tier" "$seconds" "$status")")
    failed=1
  fi
done

printf '\n\033[1;36m==> gate-full verdict\033[0m\n'
printf '%s\n' "${verdicts[@]}"
exit "$failed"
