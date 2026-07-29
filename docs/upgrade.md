# Upgrade

Upgrading the hub is **restart-based**: pull a new image tag, recreate the container.
There is no rolling upgrade, no in-place hot-swap, and no draining period — the
short outage while the new container migrates and starts is the whole cost, and
it is designed to be safe to eat.

This is the container-image deployment's upgrade story
([`docs/install.md`](./install.md)). The colocated wheel + systemd deployment
upgrades the same way in spirit — install the new wheel, restart the units — see
[`docs/deployment.md`](./deployment.md).

## The contract: runners ride out the restart by design

A runner reaches the hub outbound-only and never assumes it is always reachable:

- **Outbound facts buffer through an outage.** A runner's usage/event/completion
  traffic is store-and-forward — if the hub is down for the seconds an upgrade
  takes, the runner keeps working and flushes its buffer once the hub answers
  again. Nothing is lost, and nothing double-sends (idempotent on replay).
- **Held chunks are not dropped.** A chunk a runner is mid-node on stays claimed;
  the runner's own recovery pass (the same one that survives a `kill -9`,
  [`docs/deployment.md`](./deployment.md#the-colocated-topology)) re-reads state
  once the hub is back rather than assuming it lost the work.

So a hub restart during an upgrade is not a special case runners need scheduling
around — it is the same recovery path a crash already has to survive, exercised
on purpose instead of by accident.

## Before you upgrade

**Read the release's Upgrade notes first** — every GitHub Release's notes lead
with an **Upgrade notes** section (after any **Breaking changes** section, when
the release has one), placeholder when the release asks nothing of the
operator, hand-written prose when it does (`docs/versioning.md`). Check it
before pulling:

```bash
gh release view v0.2.0 --repo paul-gross/blizzard
```

`docs/versioning.md` names what "breaking" means and the supported skew window a
runner may lag its hub by.

## Procedure (compose deployment)

```bash
cd packaging/docker

# 1. Read the Upgrade notes (above) — act on anything it asks first.

# 2. Pull the new tag.
docker pull ghcr.io/paul-gross/blizzard-hub:v0.2.0

# 3. Point the deployment at it.
#    In .env: BLIZZARD_HUB_IMAGE=ghcr.io/paul-gross/blizzard-hub:v0.2.0

# 4. Recreate. The entrypoint migrates the store to the new head before serving
#    (bzh:manual-migrations) — the daemon refuses to start on a revision
#    mismatch, so this step is what makes that refusal moot.
docker compose up -d hub

# 5. Confirm it's serving at the new version.
curl -s https://<your-domain>/api/health | jq .version
curl -s https://<your-domain>/api/ready
```

postgres and Caddy are untouched — only the `hub` service recreates. If the new
image's migration fails, the container exits and `docker compose logs hub` shows
why; the store is unchanged (migrations run in a transaction) and the previous
tag is still pullable — see [`docs/rollback.md`](./rollback.md).

## What does *not* survive a naive upgrade

Nothing, if you're upgrading correctly: every durable path is a named volume
(`docs/backup.md` enumerates them), untouched by `docker compose up -d hub`
recreating one service. The only state genuinely at risk is state you never
committed to a volume in the first place — a locally-built image tag that was
never pushed anywhere, for instance.
