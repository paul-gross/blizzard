# Install — the reference compose deployment

The quickstart for running blizzard from the published container image: the hub, postgres, and a Caddy front terminating
TLS, via `docker compose`. This is also the shape this project runs off-machine — walked here end to end, one owner for
the whole quickstart (`docs/upgrade.md`, `docs/rollback.md`, and `docs/backup.md` all route back here rather than
restating it).

The alternative colocated wheel + systemd install (hub and runner side by side, no containers) is
[`docs/deployment.md`](./deployment.md) — pick that path instead if you are running the runner too; this compose file
ships the hub only.

## Prerequisites

- Docker with the `compose` plugin (`docker compose version`).
- A DNS name pointed at this host, for real TLS — or skip that and use the localhost evaluation profile below.

## Walkthrough

All commands run from `packaging/docker/`.

```bash
cd packaging/docker
cp .env.example .env
```

Edit `.env`:

- **`BLIZZARD_SITE_ADDRESS`** — the DNS name Caddy requests a certificate for (e.g. `blizzard.example.com`). Leave at
  the default `:80` for the localhost evaluation profile below — no TLS, no domain needed.
- **`POSTGRES_PASSWORD`** — change it before anything but evaluation.
- **`BLIZZARD_HUB_IMAGE`** — defaults to the published GHCR tag. Until the first publish (or to try a local change),
  build and point at a local tag instead:

  ```bash
  cd ../..                      # repo root
  mise run build                 # populates dist/blizzard-*.whl
  docker build -f packaging/docker/Dockerfile -t blizzard-hub:local .
  cd packaging/docker
  # then set BLIZZARD_HUB_IMAGE=blizzard-hub:local in .env
  ```

Bring the stack up:

```bash
docker compose up -d
```

Three services start, in dependency order: **postgres** (health-gated on `pg_isready`), then **hub** (waits on
postgres's health before it migrates — never migrates against a database that isn't up), then **caddy**, fronting the
hub. Watch it come up:

```bash
docker compose logs -f hub
```

## Serving

- **With a real domain** (`BLIZZARD_SITE_ADDRESS` set to a DNS name pointed at this host): Caddy mints and renews a TLS
  certificate automatically. Visit `https://<your-domain>/` for the board; `https://<your-domain>/api/ready` should
  report `"ready":true`.
- **Localhost evaluation** (`BLIZZARD_SITE_ADDRESS=:80`, the default): plain HTTP, no domain needed.

  ```bash
  curl http://localhost/api/ready
  ```

  should report `"ready":true`, and `http://localhost/` serves the board.

The hub itself publishes no ports — it is reachable only through Caddy (`packaging/docker/compose.yaml`), so
`https://<domain>/api/health` and `/api/ready` are the liveness/readiness checks to script against, not a direct hub
port.

## What's durable

Every path that must survive a restart is a named docker volume — `docker compose
down` (without `-v`) followed by `up`
loses nothing:

| Volume                        | Holds                                                                                                                         |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `postgres-data`               | The hub's store (chunks, facts, questions, everything the board reads).                                                       |
| `hub-data`                    | Signing keys (if OAuth login is later configured) and hub-command-node scratch workdirs (reclaimable — see `docs/backup.md`). |
| `caddy-data` / `caddy-config` | The minted TLS certificate and Caddy's own state, so a restart doesn't re-request one.                                        |

`blizzard-hub.toml` (bind-mounted read-only from `packaging/docker/`) is declarative, not scaffolded — it is what makes
`trusted_proxies` trust exactly this compose network's subnet, so Caddy's forwarded headers (cookie `Secure` flag,
login-throttle key, auth-fact actor IP) are honored. See `packaging/docker/README.md` for the full mount and environment
variable reference, and [`docs/deployment/install.md`](./deployment/install.md)'s "Overriding config values from the
environment" for the override precedence `BZ_HUB_DB_URL`/`BZ_HUB_HOST`/`BZ_HUB_PORT` resolve under.

## Next

- **Adding a runner** against this hub, from any machine: [`docs/remote-runner.md`](./remote-runner.md).
- **Upgrading**: [`docs/upgrade.md`](./upgrade.md).
- **Rolling back**: [`docs/rollback.md`](./rollback.md).
- **Backing up**: [`docs/backup.md`](./backup.md).
- **Configuring a work source**, **authentication**, and other operator topics not specific to this deployment shape:
  [`docs/deployment.md`](./deployment.md).
