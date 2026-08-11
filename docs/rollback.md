# Rollback

**Every shipped schema revision has a working downgrade** — the promise
`tests/test_store_migrations.py::test_migrate_up_and_down` proves mechanically
for every revision in the tree, and `docs/versioning.md` states as one of the
things a breaking release is not allowed to violate. This walks that promise
against a live compose deployment ([`docs/install.md`](./install.md)): the
previous image tag, plus a `migrate --down` to the matching revision.

Registered as the manual method `blizzard:manual-rollback-drill` in the
`blizzard-context` repo's
[verification matrix](https://github.com/paul-gross/blizzard-context/blob/master/verification/blizzard.md) — run
this end to end at least once against the compose stack, not just read.

## Why the downgrade runs on the *new* image, not the old one

A revision's `downgrade()` function — the code that knows how to reverse it —
ships in whichever image build introduced that revision. The **new** image (the
one you're rolling back *from*) carries every downgrade step back to the old
image's head; the **old** image's migration tree has never heard of the
revisions you're reversing. So: stop the hub, run the downgrade using the
still-current (new) image, *then* swap the image tag.

## Procedure

```bash
cd packaging/docker

# 1. Stop the hub — postgres and Caddy stay up. Migrations run offline
#    (bzh:manual-migrations); nothing should be writing to the store mid-downgrade.
docker compose stop hub

# 2. Learn the revision the target (older) tag's code expects at its head — no
#    store touched, just reads the image's own packaged migration tree.
docker run --rm --entrypoint python3 ghcr.io/paul-gross/blizzard-hub:v0.1.0 -c "
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.store import MIGRATIONS_DIR
print(MigrationRunner(script_location=MIGRATIONS_DIR, url='sqlite:////dev/null').script_head())
"
# -> e.g. 20260713_1218_hub_walking_skeleton

# 3. Downgrade the store to that revision, using the CURRENTLY-configured
#    (newer) image — an explicit command bypasses the entrypoint's normal
#    init/migrate/host boot sequence and execs it directly instead.
docker compose run --rm hub blizzard-hub migrate --dir /var/lib/blizzard/hub \
  --down 20260713_1218_hub_walking_skeleton

# 4. Point the deployment at the rollback tag.
#    In .env: BLIZZARD_HUB_IMAGE=ghcr.io/paul-gross/blizzard-hub:v0.1.0

# 5. Bring the hub back up on the old image. Its entrypoint's own `migrate`
#    step is then a no-op — the store is already at exactly its head.
docker compose up -d hub

# 6. Confirm.
curl -s https://<your-domain>/api/health | jq .version   # -> 0.1.0
curl -s https://<your-domain>/api/ready                  # -> "ready":true
```

## What a rollback does not undo

- **Data written under the newer schema that the older code never reads.** A
  downgrade reverses schema shape; it does not resurrect meaning the old code
  never had a column for. This is exactly what `docs/versioning.md` calls
  breaking, and why an unreversible migration is a breaking release by
  definition — it should never reach this doc's "every revision has a working
  downgrade" guarantee in the first place.
- **Anything outside the store** — signing keys, hub workdirs
  ([`docs/backup.md`](./backup.md)) are untouched by a schema downgrade; a
  rollback is a store-schema operation, not a full-state restore. If a bad
  release also corrupted state outside the store, restore from backup instead.

## See also

- [`docs/upgrade.md`](./upgrade.md) — the forward direction.
- [`docs/versioning.md`](./versioning.md) — what "breaking" means, including the
  unreversible-migration case this doc's guarantee depends on never happening.
