# Backup

The durable-state inventory, read off the code, for the compose deployment
([`docs/install.md`](./install.md)) — what to snapshot, what to skip, and how to
restore for both store backends.

## What is durable

| Path | Volume | What it is | Back up? |
|---|---|---|---|
| The store — the whole `postgres-data` volume (the compose deployment's default) *or* `data/hub.db` (only if you moved `BZ_HUB_DB_URL` back to sqlite) | `postgres-data` / `hub-data` | Every chunk, fact, question, and graph the board reads. The only irreplaceable state. | **Yes** |
| `data/auth/signing-keys/` | `hub-data` | The IdP RSA keypair(s) + `meta.json` (`src/blizzard/hub/auth/signing.py`) — only populated once `auth.mode = "oauth"` is configured. Losing it invalidates every live session and forces a re-login fleet-wide (a runner's JWKS cache re-fetches on an unknown `kid`, but the key itself doesn't come back). | **Yes**, once OAuth login is configured |
| `blizzard-hub.toml` | bind-mounted from `packaging/docker/blizzard-hub.toml` | The deployment's config. In the **compose** deployment this file is git-tracked source, not volume state — already "backed up" by being in version control. (The colocated wheel/systemd deployment writes it directly into the runtime dir instead — back it up there.) | Already versioned (compose) / **Yes** (systemd) |
| `data/hub_workdirs/` | `hub-data` | Scratch git clones a hub command node uses mid-delivery (`config.data_dir / "hub_workdirs"`, `src/blizzard/hub/app.py`). | **No — reclaimable.** Re-cloned from the delivery forge on next use; carries no state the store doesn't already have a record of. |
| `caddy-data` / `caddy-config` | named volumes | The minted TLS certificate + Caddy's own state. | Optional — losing it just costs one re-issuance from Let's Encrypt on next boot, not data loss. |

`packaging/docker/compose.yaml` pins the compose project name to `blizzard`
(`name: blizzard`), so the volumes above resolve to fixed, documentable names:
`blizzard_hub-data`, `blizzard_postgres-data`, `blizzard_caddy-data`,
`blizzard_caddy-config` — confirm with `docker compose -f
packaging/docker/compose.yaml config --volumes` or `docker volume ls` if you ever
run compose with a `-p`/`COMPOSE_PROJECT_NAME` override, which takes precedence
over the pinned name.

## Snapshot and restore — postgres (the compose deployment's default store)

`pg_dump`/`pg_restore` on a live cluster, no stop required — postgres's own MVCC
gives you a consistent snapshot without pausing the hub. Run the dump *inside*
the postgres container via `sh -c` so `$POSTGRES_USER`/`$POSTGRES_DB` resolve
against the container's own environment (compose injects them from `.env`) —
they are not set in your shell:

```bash
cd packaging/docker
docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > hub-postgres-backup.dump
```

```bash
# Restore into a fresh (or emptied) database:
cd packaging/docker
docker compose stop hub
docker compose exec -T postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
  < hub-postgres-backup.dump
docker compose up -d hub
```

## Snapshot and restore — the `hub-data` volume (signing keys, config-adjacent state)

Signing keys and hub workdirs live outside the store regardless of which store
backend you run, on the `blizzard_hub-data` volume:

```bash
cd packaging/docker

# Snapshot: stop the hub first — a live write mid-tar is not a safe copy.
docker compose stop hub
docker run --rm -v blizzard_hub-data:/from -v "$(pwd)":/to alpine \
  tar czf /to/hub-data-backup.tgz -C /from .
docker compose start hub
```

```bash
# Restore into a fresh volume:
docker compose stop hub
docker volume rm blizzard_hub-data   # only once you're sure — this is destructive
docker volume create blizzard_hub-data
docker run --rm -v blizzard_hub-data:/to -v "$(pwd)":/from alpine \
  tar xzf /from/hub-data-backup.tgz -C /to
docker compose up -d hub
```

To snapshot only the signing keys (skipping the reclaimable `hub_workdirs/`):

```bash
docker run --rm -v blizzard_hub-data:/from -v "$(pwd)":/to alpine \
  tar czf /to/signing-keys-backup.tgz -C /from auth/signing-keys
```

If you moved the store back to sqlite (`BZ_HUB_DB_URL` unset — `data/hub.db`
under this same volume), the tar above already carries it too; stop the hub
first as shown, exactly as for the signing-keys-only case.

## See also

- [`docs/upgrade.md`](./upgrade.md), [`docs/rollback.md`](./rollback.md) — the
  other two operator documents this one joins.
- [`packaging/docker/README.md`](../packaging/docker/README.md) — the one-mount
  summary this table expands on; this file is the durable-path inventory's
  single owner — update here first when a path moves.
