# Upgrade

Upgrading the hub is restart-based — pull a new image tag, recreate the container — with no rolling, hot-swap, or
draining path. The brief outage while the new container migrates and starts is the whole cost. What follows is the
reference compose deployment ([`docs/install.md`](./install.md)).

## Before you pull

Read the release's **Upgrade notes** and act on whatever it asks:

```bash
gh release view v0.2.0 --repo paul-gross/blizzard
```

[`docs/versioning.md`](./versioning.md) owns what counts as breaking and the skew window a runner may lag its hub by.

## Pull and recreate

From `packaging/docker/`, pull the tag:

```bash
docker pull ghcr.io/paul-gross/blizzard-hub:v0.2.0
```

Set `BLIZZARD_HUB_IMAGE` to that tag in `.env`, then recreate the hub alone, leaving postgres and Caddy untouched:

```bash
docker compose up -d hub
```

Recreation is what migrates: the entrypoint migrates the store to the new head before serving, and the daemon refuses to
start on a revision mismatch.

Confirm the new version is serving:

```bash
curl -s https://<your-domain>/api/health | jq .version
curl -s https://<your-domain>/api/ready
```

A failed migration exits the container with the reason in `docker compose logs hub`, leaving the store unchanged because
migrations run in a transaction; the previous tag stays pullable ([`docs/rollback.md`](./rollback.md)).

## Why the restart is safe while runners are mid-work

A runner reaches the hub outbound-only and never assumes it is reachable — that is what makes the restart safe.

A runner's usage, event, and completion traffic is store-and-forward: it keeps working and buffers while the hub is
down, then flushes once the hub answers, losing nothing and double-sending nothing because replay is idempotent.

A chunk a runner is mid-node on stays claimed across the outage, and the runner's recovery pass — the same one that
survives a `kill -9` ([`docs/deployment.md`](./deployment.md#the-colocated-topology)) — re-reads state when the hub
returns instead of assuming the work was lost.

A worker's hub-proxied read — the runner-local routes that forward to the hub on a worker's behalf — rides the
restart out too: the forward retries with bounded backoff instead of surfacing the restart window's `502` straight
to the worker, so a call landing mid-swap still answers once the hub comes back.
