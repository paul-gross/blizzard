# Rollback

Rolling back the compose deployment ([`docs/install.md`](./install.md)) is two moves: reverse the store to the older
tag's schema revision with `migrate --down`, then put the older image tag back. The order is fixed — stop the hub,
downgrade on the new image, swap the tag, then start the hub again.

The guarantee this rests on — that every schema revision blizzard ships keeps a working downgrade — is owned by
[`docs/versioning.md`](./versioning.md).

Rehearse this end to end against a real compose stack before you need it.

## 1. Stop the hub

From `packaging/docker/`:

```bash
docker compose stop hub
```

Postgres and Caddy keep running. Migrations run offline, and nothing should be writing to the store mid-downgrade.

## 2. Read the revision the older tag expects

The revision the target tag expects at its head comes straight out of that image's packaged migration tree, touching no
store:

```bash
docker run --rm --entrypoint python3 ghcr.io/paul-gross/blizzard-hub:v0.1.0 -c "from blizzard.foundation.store.migrations import MigrationRunner; from blizzard.hub.store import MIGRATIONS_DIR; print(MigrationRunner(script_location=MIGRATIONS_DIR, url='sqlite:////dev/null').script_head())"
```

It prints a revision id such as `20260713_1218_hub_walking_skeleton`.

## 3. Downgrade, on the new image

Reverse the store to that revision:

```bash
docker compose run --rm hub blizzard-hub migrate --dir /var/lib/blizzard/hub --down 20260713_1218_hub_walking_skeleton
```

The downgrade must be run using the **new** image — the one being rolled back from — because a revision's `downgrade()`
code ships in whichever image build introduced that revision.

An explicit command passed to `docker compose run` bypasses the entrypoint's init-migrate-serve boot and execs that
command instead, which is what runs the downgrade without booting the daemon.

## 4. Put the older tag back

Set `BLIZZARD_HUB_IMAGE=ghcr.io/paul-gross/blizzard-hub:v0.1.0` in `.env`, then bring the hub up:

```bash
docker compose up -d hub
```

On the way back up the old image's own entrypoint migration is a no-op — the store already sits at exactly its head.

## 5. Confirm

```bash
curl -s https://<your-domain>/api/health | jq .version
curl -s https://<your-domain>/api/ready
```

The first reports the older version, the second `"ready":true`.

## What a rollback does not undo

A downgrade reverses schema shape, not meaning: data written under the newer schema that the older code has no column
for does not come back.

It also leaves everything outside the store untouched — signing keys, hub workdirs — so a rollback is not a restore. If
a bad release also corrupted state outside the store, restore from backup ([`docs/backup.md`](./backup.md)).
