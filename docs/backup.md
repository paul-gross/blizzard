# Backup

The durable-state inventory, read off the code, for the compose deployment
([`docs/install.md`](./install.md)) — what to snapshot, what to skip, and how to
restore for both store backends.

## What is durable

| Path | Volume | What it is | Back up? |
|---|---|---|---|
| The store — `data/hub.db` (sqlite) *or* the whole `postgres-data` volume (postgres) | `hub-data` (sqlite case) / `postgres-data` | Every chunk, fact, question, and graph the board reads. The only irreplaceable state. | **Yes** |
| `data/auth/signing-keys/` | `hub-data` | The IdP RSA keypair(s) + `meta.json` (`src/blizzard/hub/auth/signing.py`) — only populated once `auth.mode = "oauth"` is configured. Losing it invalidates every live session and forces a re-login fleet-wide (a runner's JWKS cache re-fetches on an unknown `kid`, but the key itself doesn't come back). | **Yes**, once OAuth login is configured |
| `blizzard-hub.toml` | bind-mounted from `packaging/docker/blizzard-hub.toml` | The deployment's config. In the **compose** deployment this file is git-tracked source, not volume state — already "backed up" by being in version control. (The colocated wheel/systemd deployment writes it directly into the runtime dir instead — back it up there.) | Already versioned (compose) / **Yes** (systemd) |
| `data/hub_workdirs/` | `hub-data` | Scratch git clones a hub command node uses mid-delivery (`config.data_dir / "hub_workdirs"`, `src/blizzard/hub/app.py`). | **No — reclaimable.** Re-cloned from the delivery forge on next use; carries no state the store doesn't already have a record of. |
| `caddy-data` / `caddy-config` | named volumes | The minted TLS certificate + Caddy's own state. | Optional — losing it just costs one re-issuance from Let's Encrypt on next boot, not data loss. |

## Snapshot and restore — sqlite (the default store)

```bash
cd packaging/docker

# Snapshot: stop the hub first — a live sqlite file mid-write is not a safe copy.
docker compose stop hub
docker run --rm -v blizzard-hub-data:/from -v "$(pwd)":/to alpine \
  tar czf /to/hub-data-backup.tgz -C /from .
docker compose start hub
```

```bash
# Restore into a fresh volume:
docker compose stop hub
docker volume rm blizzard-hub-data   # only once you're sure — this is destructive
docker volume create blizzard-hub-data
docker run --rm -v blizzard-hub-data:/to -v "$(pwd)":/from alpine \
  tar xzf /from/hub-data-backup.tgz -C /to
docker compose up -d hub
```

(`blizzard-hub-data` is the actual volume name compose derives — `docker volume ls`
to confirm the `<project>_hub-data` name in your deployment; `docker compose config
--volumes` also lists it.)

## Snapshot and restore — postgres

`pg_dump`/`pg_restore` on a live cluster, no stop required — postgres's own MVCC
gives you a consistent snapshot without pausing the hub:

```bash
cd packaging/docker
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --format=custom > hub-postgres-backup.dump
```

```bash
# Restore into a fresh (or emptied) database:
cd packaging/docker
docker compose stop hub
cat hub-postgres-backup.dump | docker compose exec -T postgres pg_restore \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists
docker compose up -d hub
```

## Signing keys (once OAuth login is configured)

Included in the sqlite snapshot above (same `hub-data` volume) automatically. If
you back up the postgres store separately from the volume tar, still snapshot
`data/auth/signing-keys/` on its own — it never lives in the store:

```bash
docker run --rm -v blizzard-hub-data:/from -v "$(pwd)":/to alpine \
  tar czf /to/signing-keys-backup.tgz -C /from auth/signing-keys
```

## See also

- [`docs/upgrade.md`](./upgrade.md), [`docs/rollback.md`](./rollback.md) — the
  other two operator documents this one joins.
- [`packaging/docker/README.md`](../packaging/docker/README.md) — the mount
  reference this table is derived from.
