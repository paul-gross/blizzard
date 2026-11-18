---
kind: deprecated
surfaces: [config]
removes-in: "0.6"
---

The runner hub token sources `BZ_HUB_TOKEN_<NAME>`, the host file's `[hub_tokens]` table and the `BZ_HUB_TOKEN_<NAME>`
lines of a host directory's `.env` are deprecated; the host store holds each runner's token, set with
`blizzard runner token set`. The host still reads them, only to import a token a runner does not hold in its store yet,
and warns at start while one holds a token for a runner whose token is stored.

## Upgrade

After the first start of the new release, set any later token with `blizzard runner token set`, then remove the runner's
line from `.env`, its `[hub_tokens]` entry, or its `BZ_HUB_TOKEN_<NAME>` variable.
