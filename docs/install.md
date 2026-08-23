# Install

This is the quickstart for running blizzard from the published container image via `docker compose` — the hub, postgres,
and a Caddy front terminating TLS. The one prerequisite is Docker with the `compose` plugin.

The stack is three services — postgres, hub, and caddy — with the hub gated on postgres reporting healthy, so it never
runs its migrations against a database that is not up. It ships the hub only; an operator who wants the runner on the
same machine takes the colocated wheel + systemd path in [`docs/deployment.md`](./deployment.md) instead.

## Configure

Configuration starts from the shipped example, and the `cd` below establishes the directory every remaining command in
this quickstart runs from:

```bash
cd packaging/docker && cp .env.example .env
```

`POSTGRES_PASSWORD` must be changed before any use beyond local evaluation.

`BLIZZARD_SITE_ADDRESS` decides the serving mode. A DNS name already pointed at this host — say `blizzard.example.com` —
gets real TLS that Caddy mints and renews unprompted. The default `:80` gets a localhost evaluation profile over plain
HTTP, with no domain needed.

`.env.example`'s own inline comments are the per-key reference for every variable, including the `BLIZZARD_HUB_IMAGE`
override that points the deployment at a locally built image instead of the published GHCR tag. The image's full mount
and environment-variable reference lives in [`packaging/docker/README.md`](../packaging/docker/README.md).

`blizzard-hub.toml` is bind-mounted read-only from `packaging/docker/` and is declarative rather than scaffolded. Its
`trusted_proxies` names this compose network's subnet, which is what makes Caddy's forwarded headers trusted.

## Bring it up

```bash
docker compose up -d
```

The hub container publishes no host port and is reachable only through Caddy, so everything is checked at the site
address. `/` serves the board, and `/api/ready` reports `"ready":true` — on the evaluation profile,
`curl http://localhost/api/ready`. It and `/api/health` are the endpoints to script liveness and readiness against.

## Durable state

Every path that has to survive a restart lives on a named docker volume, so `docker compose down` without `-v` followed
by `up` loses nothing. [`docs/backup.md`](./backup.md) owns the inventory of which state is durable, which is
reclaimable, and how each is snapshotted and restored.

## Adding a runner

Adding a runner against this hub, from any machine, is [`docs/remote-runner.md`](./remote-runner.md).
