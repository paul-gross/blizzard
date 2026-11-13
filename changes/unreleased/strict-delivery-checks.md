---
kind: fixed
surfaces: [http-api, cli]
---

Automatic delivery now requires observed passing or neutral CI checks by default, including on existing repositories.
Empty results get a ten-minute registration grace before escalation; repositories intentionally without CI can opt in
with `blizzard hub repo edit <repository> --allow-no-ci`. After changing a setting for an escalated delivery, requeue
the chunk for a fresh visit. See `docs/deployment/delivery-policy.md` for diagnostics and recovery.

## Upgrade

Grant GitHub fine-grained tokens repository Commit statuses read as well as Checks read, or delivery cannot read both
result types. For a manually operated hub, back up the store and run `blizzard hub migrate --dir <hub-dir>` with the new
build before starting it; packaged deployments migrate on startup.
