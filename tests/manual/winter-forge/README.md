# Winter-wired forge acceptance exercise

This explicit manual exercise uses fresh, controlled-clock hub and stepped runner processes, not the environment's hub
or runner. The fresh hub points its repository and work-source configuration at the **standing winter forge**. Each test
creates a uniquely named repository through `POST /_repos`, and changes only that repository's levers and origin. The
standing forge itself is not clock-controlled or restarted. This supplements, not replaces, the service tier.

Provision the assigned feature environment first, and start its forge using winter service tooling. Do not reset or
restart an occupied environment, its forge, or its runner for this exercise. Source its environment and confirm the
forge is loopback and its origins are the assigned disposable fixture. Set `BLIZZARD_TMPDIR` to an absolute scratch
directory. From the workspace root, for example:

```bash
source <(winter env bz4)
export BLIZZARD_TMPDIR=/absolute/assigned/scratch
curl -fsS "http://127.0.0.1:$BZ_FORGE_PORT/healthz"
cd bz4/blizzard
mise run playwright -- -c playwright.winter-forge.config.ts --workers=1
```

The test prints each newly created origin, chunk, candidate and landed SHAs, snapshotted terms, and durable landing
facts. It exercises both automatic policies, the ten-minute grace on the hub clock, opt-in and requeue via CLI, denied
and pending statuses, superseded failure, and manual merge reconciliation. Origin heads and the hub's landing-facts
endpoint are compared before and after each decision. Playwright's run files live beneath `BLIZZARD_TMPDIR`. Retain the
console output with the verification evidence. The forge has no repository-delete control: the unique origins and
repository state remain in the assigned disposable fixture; do not destroy or reset that shared fixture as cleanup.
