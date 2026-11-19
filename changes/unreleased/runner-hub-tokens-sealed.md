---
kind: changed
surfaces: [config, store]
upgrade-guide: runner-hub-tokens-move-into-the-host-store
---

Each runner's hub token now lives in the runner host's store, sealed under the host's key, which `BZ_HOST_SECRET_KEY`
supplies or the host mints into `data/secret-keys/`. Each start imports the token of a runner that has none stored from
`BZ_HUB_TOKEN_<NAME>`, `[hub_tokens]` or `.env`, and never reads those sources for it again. A replaced token takes
effect at the runner's next identity check, with no restart, and an ingest re-seals the runner's token under the
destination host's key instead of writing it to that host's `.env`. A host whose key no longer opens a runner's token
still starts; that runner refuses until its token is set again.

## Upgrade

Back up and migrate each runner store before starting the new release, and back up `data/secret-keys/` with it from the
first start on, or supply `BZ_HOST_SECRET_KEY`. Change a runner's token with `blizzard runner token set` from then on.
Follow the linked upgrade guide for rollback.
