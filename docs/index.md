# Docs

The operator- and release-facing prose docs under `docs/` — route from here. Each
file below is a single owner; a fact stated in one is linked from the others,
never restated. (`designs/` and `identity/` are visual-asset directories, not
prose documentation, and aren't routed here.)

| File | Read when… |
|------|-------------|
| [`install.md`](./install.md) | …standing up blizzard for the first time — the reference `docker compose` deployment: hub, postgres, Caddy. Start here. |
| [`deployment.md`](./deployment.md) | …installing the colocated wheel + systemd alternative instead (hub and runner side by side, no containers), or configuring anything not specific to one deployment shape — work sources, runner authentication, human auth, cost caps, the kiosk demo mode, the recovery contract. |
| [`remote-runner.md`](./remote-runner.md) | …adding a runner on a machine that is not the hub's — pointing `hub_url` across the network, choosing a `runner_id`, enrolling the newcomer, and what HTTPS and distance change. |
| [`upgrade.md`](./upgrade.md) | …pulling a new image tag — the restart-based contract and the runner ride-out guarantee that makes it safe. |
| [`rollback.md`](./rollback.md) | …a release needs reversing — the previous image tag plus a `migrate --down`, walked end to end. |
| [`backup.md`](./backup.md) | …snapshotting or restoring durable state — the full inventory of what's durable, what's reclaimable, and the commands for both store backends. |
| [`versioning.md`](./versioning.md) | …you need to know what a version number promises: the semver scheme, what counts as breaking, and the supported hub↔runner skew. |
| [`ci.md`](./ci.md) | …touching a GitHub Actions workflow, or reproducing the merge gate locally. |

## See also

- [`packaging/docker/README.md`](../packaging/docker/README.md) — the container
  image's own mount and environment-variable reference.
- The `blizzard-context` repo's verification matrix (`blizzard-context:/verification/blizzard.md`)
  and `bzh:release` (`blizzard-context:/workflows/release.md`) — how a change is
  proven, and the release-cut sequence these documents are the operator-facing
  half of.
