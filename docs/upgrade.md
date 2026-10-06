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

### Subscription-usage response boundary

One release removes `RunnerView.external_subscription_usage` — the singular usage response — leaving the per-slug
`subscriptions` collection as the only subscription surface. The release that does it marks the removal under **Breaking
changes**, and its **Upgrade notes** name the hub version that drops the field and the minimum runner version that
reports per-subscription samples; read those before pulling, because the two facts below are what make this boundary
safe to cross.

**Order.** Upgrade every runner to slug-carrying, per-subscription reporting **before** deploying the hub. This is a
user-approved exception to the skew window in [`docs/versioning.md`](./versioning.md) — a runner may normally lag its
hub by one minor version, and across this one boundary it may not. API clients must read `subscriptions`; the removed
field is gone from `openapi/hub.openapi.json` and the generated TypeScript client in the same release.

**If a runner is still pre-boundary.** Its usage samples carry no `slug`, and the hub **rejects** them — it does not
silently discard them. Each rejected sample is named in the batch ack, and the runner logs `hub rejected buffered fact`
at error severity once per sample interval. That log is the signal, not a new fault: the drain acks and moves on rather
than wedging, so nothing backs up and no other traffic is affected. The runner's subscription block stays empty on the
board for as long as it lasts.

**Getting back.** The condition clears on its own the moment that runner is upgraded — there is no hub-side cleanup and
no data to repair. Confirm the runner is reporting again with `blizzard runner external-usage probe <slug>`
([`docs/deployment/spend.md`](./deployment/spend.md)), then check that the subscription reappears on the runner's board
panel.

**A separate case.** A sample that *does* carry a slug but whose windows are unusable is accepted, not rejected: the bad
windows are dropped at intake and the subscription renders **present with no windows**, which is not the same as a
pre-boundary runner's absent block. Both are logged at warning severity on the hub with the runner and slug.

**`sampled_at` can be null.** `SubscriptionUsageView.sampled_at` is now nullable: for a miss-only subscription row — one
with no successful sample, whose credential has lapsed — it is `null`, and `condition` carries the reason. This is
deliberate and counts as breaking on the HTTP API, so an API client that reads `sampled_at` as always present must
handle null and read `condition` in that case.

### File-configured work sources boundary

A hub crossing into store-held work sources refuses to start while its `blizzard-hub.toml` keeps `[[work_source]]`
blocks or its container environment keeps `BZ_FORGE_*` variables. Carry them into records before recreating the
container — [`docs/deployment/install.md`](./deployment/install.md#work-sources-and-forge-settings-are-records) owns the
step.

### Capability-reporting boundary

A runner that reports no capabilities is eligible for nothing. This is breaking on the hub↔runner wire, because the
empty `capabilities` default used to mean "no filter".

**Order.** Upgrade every runner to capability-reporting **before** deploying the hub.

**If a runner is still pre-boundary.** It registers, but the matched peek never returns it an entry and every claim it
makes is refused with a `409` naming it as `incompatible_runner_id`.

**Getting back.** The condition clears the moment that runner is upgraded; there is nothing to repair on the hub.

### Runner identity boundary

One release keys every runner by an id the hub mints, and retires `runner_auth_mode`. From that release on:

- **Every runner authenticates.** A fleet call without a token the hub issued is refused with a `401` under every
  configuration. A `blizzard-hub.toml` that still sets `runner_auth_mode` boots with the key ignored; delete it whenever
  convenient.
- **Ids are hub-minted.** The hub's migration gives every runner an `rn_` id and keeps its old id as its name. Each
  enrolled token resolves to its runner's new id, so no runner re-enrolls. Operator verbs and `/api/runners/{id}` take
  the `rn_` id, which `hub runner list` shows beside each name, so a script that passed an old id passes the new one.
- **A runner that held no token** — one a `warn` hub admitted — keeps its history under its new id, but is refused until
  `blizzard hub runner enroll <id>` mints its token. Put the line `enroll` prints in the runner's runtime-dir `.env`.
- **The name is the runner's own.** A `blizzard-runner.toml` that declares a `runner_id` and no `name` reads that value
  as the name, so no config needs editing; a runner renames itself by changing `name` and restarting
  ([remote-runner.md](./remote-runner.md#point-it-at-the-hub)).
- **`runner init` adds the runner** at the hub its config names and writes its token
  ([runner-auth.md](./deployment/runner-auth.md#adding-a-runner)). Anything that re-runs init on a runner directory that
  outlives its hub's data passes `--allow-readd`.
- **Exported and traced runner ids change.** The egress `steps` and `invocations` rows and the `blizzard.runner.id` span
  attribute carry the `rn_` id from the upgrade on, each with the runner's name beside it.
  [egress.md](./deployment/egress.md#upgrading-to-hub-minted-runner-ids) owns re-exporting history under the new ids,
  and [tracing.md](./deployment/tracing.md#upgrading-to-hub-minted-runner-ids) owns querying across the upgrade.

**Order.** Across this boundary a runner may not lag its hub, the second exception
[`docs/versioning.md`](./versioning.md#the-hubrunner-skew-window) records. Let the chunks in flight finish, stop every
runner, and take a hub store backup ([`docs/backup.md`](./backup.md)). Upgrade the hub, which migrates on boot. Then
upgrade each runner: its store migration runs offline before the daemon starts, as the packaged unit's `ExecStartPre`
runs it. Start the runners; each registers with the token it already holds and keeps the id the hub minted for it.

**If a runner is still pre-boundary.** It is not supported against the upgraded hub; keep it stopped until it is
upgraded.

**Getting back.** A rollback across this boundary is lossy for runner ids;
[`docs/rollback.md`](./rollback.md#what-a-rollback-does-not-undo) names what does not come back.

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
down, then flushes once the hub answers, losing nothing and double-sending nothing because replay is idempotent. The
intentional exception is a pre-boundary slug-less subscription sample, which the hub rejects rather than stores — the
runner acks it and moves on instead of wedging the queue, so the rest of the buffer still flushes. Upgrade runners
before the hub; see the subscription-usage response boundary above.

A chunk a runner is mid-node on stays claimed across the outage, and the runner's recovery pass — the same one that
survives a `kill -9` ([`docs/deployment.md`](./deployment.md#the-colocated-topology)) — re-reads state when the hub
returns instead of assuming the work was lost.

A worker's hub-proxied read — the runner-local routes that forward to the hub on a worker's behalf — rides the restart
out too: the forward retries with bounded backoff instead of surfacing the restart window's `502` straight to the
worker, so a call landing mid-swap still answers once the hub comes back.
