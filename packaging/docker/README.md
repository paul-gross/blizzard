# The blizzard hub container image

`Dockerfile` builds the hub — the fleet's HTTP API, SSE stream, and embedded mission-control board — from the release
wheel (`mise run build`), which already embeds both Angular frontends and both migration trees. The image carries no
Node.

For the full install/upgrade/rollback/backup story see `docs/install.md` and `docs/deployment.md` (env-var override
precedence). This file documents the image itself.

## Building

```bash
mise run build          # populates dist/blizzard-*.whl
docker build -f packaging/docker/Dockerfile -t blizzard-hub:local .
```

## Running

```bash
docker run -d --name blizzard-hub \
  -p 8421:8421 \
  -v blizzard-hub-data:/var/lib/blizzard/hub \
  blizzard-hub:local
```

The entrypoint (`entrypoint.sh`) runs three ordered steps — scaffold the config if the volume is fresh, migrate the
store to head, then `exec` the daemon (`bzh:manual-migrations`): the daemon's own startup path never migrates, so a
wheel/image upgrade + restart self-heals exactly like the colocated systemd deployment (`packaging/systemd/`).

## The one mount

Everything durable lives under a single documented path: **`/var/lib/blizzard/hub`**.

| Under the mount           | What it holds                                                                                                                                                           |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `blizzard-hub.toml`       | The scaffolded config file — bind-mount a pre-authored one read-only for a declarative deployment (e.g. to set `trusted_proxies`, see `packaging/docker/compose.yaml`). |
| `data/hub.db`             | The sqlite store (when `db_url` is left at its default — see below).                                                                                                    |
| `data/auth/signing-keys/` | OAuth login signing keys, only written when `auth.mode = "oauth"`.                                                                                                      |
| `data/hub_workdirs/`      | Hub-command-node scratch clones — reclaimable, not backup-worthy (`docs/backup.md`).                                                                                    |

## Environment variables

The container image is configured by its runtime environment (issue #187) — see `docs/deployment.md`'s "Overriding
config values from the environment" for the full precedence rule (CLI flag > environment > toml > default).

| Variable                          | Image default                           | Purpose                                                                                                    |
| --------------------------------- | --------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `BZ_HUB_DIR`                      | `/var/lib/blizzard/hub`                 | The runtime root the entrypoint scaffolds/migrates/serves.                                                 |
| `BZ_HUB_HOST`                     | `0.0.0.0`                               | Bind host — the image default, so the daemon is reachable from outside the container without extra config. |
| `BZ_HUB_PORT`                     | *(unset — toml/built-in default, 8421)* | Bind port.                                                                                                 |
| `BZ_HUB_DB_URL`                   | *(unset — sqlite under `data/`)*        | Point at a postgres URL (see the `postgres` extra below) to run the store off-volume.                      |
| `BZ_LOG_FORMAT`                   | `json`                                  | Structured JSON logs, the shape a container log collector expects.                                         |
| `BZ_FORGE_URL` / `BZ_FORGE_TOKEN` | *(unset)*                               | The delivery forge a hub command node's `run:` script talks to — see `docs/deployment.md`.                 |

## The `postgres` extra

`pyproject.toml` declares `psycopg[binary]` as an optional `postgres` extra rather than a core dependency, so the
sqlite/systemd deployment stays lean. The image installs `blizzard[postgres]`, so pointing `BZ_HUB_DB_URL` at a postgres
URL (`bzh:sql-portable` — the store URL is the only portability knob, no code branches on the backend) needs no image
change — see `packaging/docker/compose.yaml` for a worked example.

## Health

`HEALTHCHECK` polls `GET /api/health` (a dependency-free liveness signal) with the stdlib `urllib` — no curl in the
image. `GET /api/ready` is the deeper readiness signal (store reachable and at the expected schema revision);
`docker compose`'s health-gated `depends_on` (`compose.yaml`) waits on the container healthcheck, which only reflects
liveness — `blizzard:compose-smoke` is what actually asserts readiness before serving.
