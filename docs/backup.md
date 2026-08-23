# Backup

This is the durable-state inventory for the compose deployment ([`docs/install.md`](./install.md)) and its single owner
— when a durable path moves, update it here first. [`packaging/docker/README.md`](../packaging/docker/README.md) carries
only the one-mount summary.

[`packaging/docker/compose.yaml`](../packaging/docker/compose.yaml) pins the compose project name (`name: blizzard`), so
the volumes resolve to fixed names — `blizzard_hub-data`, `blizzard_postgres-data`, `blizzard_caddy-data`,
`blizzard_caddy-config`. `-p`/`COMPOSE_PROJECT_NAME` overrides the pin; confirm with `docker volume ls` or
`docker compose -f packaging/docker/compose.yaml config --volumes`.

## What is durable

**The store** is the only irreplaceable state — every chunk, fact, question, graph, and stored transcript segment the
board reads; always back it up. It is the whole `postgres-data` volume by default, or `data/hub.db` on the `hub-data`
volume if `BZ_HUB_DB_URL` was moved back to sqlite.

**Signing keys.** `data/auth/signing-keys/` on the `hub-data` volume holds the IdP RSA keypair(s) plus `meta.json`
(`src/blizzard/hub/auth/signing.py`), populated only once `auth.mode = "oauth"` is configured — back it up from then on.
Losing the signing keys invalidates every live session and forces a fleet-wide re-login: a runner's JWKS cache
re-fetches on an unknown `kid`, but the key itself never comes back.

**Hub workdirs.** `data/hub_workdirs/` — scratch git clones a hub command node uses mid-delivery
(`config.data_dir / "hub_workdirs"`) — is reclaimable: re-cloned from the delivery forge on next use, carrying no state
the store lacks, so skip it.

Signing keys and hub workdirs live on the `blizzard_hub-data` volume regardless of which store backend runs.

**Caddy state.** The `caddy-data` and `caddy-config` volumes hold the minted TLS certificate and Caddy's own state;
backing them up is optional — losing them costs one re-issuance from Let's Encrypt on next boot, not data.

**Configuration.** The compose deployment bind-mounts `blizzard-hub.toml` from `packaging/docker/blizzard-hub.toml` —
git-tracked source, already versioned. The colocated wheel/systemd deployment instead writes it into the runtime dir,
where it does need backing up.

## The postgres store

The postgres store snapshots live, no stop required — postgres's MVCC yields a consistent dump while the hub keeps
running. Run the dump inside the postgres container via `sh -c` so `$POSTGRES_USER`/`$POSTGRES_DB` resolve from the
container's own environment (compose injects them from `.env`) — they are not set in your shell.

Snapshot, from `packaging/docker`:

```bash
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > hub-postgres-backup.dump
```

Restore into a fresh or emptied database, from `packaging/docker`:

```bash
docker compose stop hub
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < hub-postgres-backup.dump
docker compose up -d hub
```

## The hub-data volume

Stop the hub before tarring the `hub-data` volume — a live write mid-tar is not a safe copy. With the store on sqlite
(`BZ_HUB_DB_URL` unset, `data/hub.db` on this same volume), the whole-volume tar already carries the store too — stop
the hub first as always.

Snapshot:

```bash
docker compose stop hub
docker run --rm -v blizzard_hub-data:/from -v "$(pwd)":/to alpine tar czf /to/hub-data-backup.tgz -C /from .
docker compose start hub
```

To snapshot only the signing keys, skipping the reclaimable workdirs — with the hub stopped, as for any hub-data tar:

```bash
docker run --rm -v blizzard_hub-data:/from -v "$(pwd)":/to alpine tar czf /to/signing-keys-backup.tgz -C /from data/auth/signing-keys
```

Restore into a fresh volume. The hub container is removed first, not merely stopped — Docker refuses to remove a volume
any container still references, stopped ones included — and the closing `up -d` recreates it:

```bash
docker compose rm -sf hub
docker volume rm blizzard_hub-data   # destructive — only once you're sure
docker volume create blizzard_hub-data
docker run --rm -v blizzard_hub-data:/to -v "$(pwd)":/from alpine tar xzf /from/hub-data-backup.tgz -C /to
docker compose up -d hub
```
