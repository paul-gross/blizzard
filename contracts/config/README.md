# Config contract documents

The keys each daemon reads from its toml file, and the paths it keeps under its runtime root, pinned so that a change to
either is deliberate.

- `hub.json` (`blizzard-hub.toml`), `host.json` (`blizzard-host.toml`) — one document per daemon: every key as a `path`
  (the tables of an array marked `name[]`, as in `runner[].cost.chunk_cap_usd`), its `type`, whether the file must carry
  it (`required`), its `default` (`null` when it has none, or when the daemon derives one at load), and whether it is
  `deprecated` with the key or table that `replaced_by` it. Then the durable `paths`: each with what it holds, the
  `blizzard-runner` verbs that write it (`written_by`; empty when the contract names no writer), and the permission bits
  the daemon enforces on it (`mode`, a four-digit octal string such as `"0600"`; `null` when it enforces none). Then
  `env`: the env file that holds the daemon's tokens — its `file`, `mode`, whether it is rewritten `atomic`ally, and its
  `variables`, each with a `name_rule` (`prefix`, `upper_case`, `replace_pattern`, `replacement`) that derives a name
  such as `BZ_HUB_TOKEN_<NAME>` from a runner's name, or `null` for a fixed name such as `BZ_HOST_TOKEN`. `env` is
  `null` for a daemon that declares none. Never edited by hand.

A key of type `open table` has free-form children, such as `flags`, `hub_tokens` or an alias table; no key beneath it is
listed. A deprecated key is still read — to refuse it, graduate it, or honor it — so it stays declared.

`blizzard-export-contracts` builds the documents from each daemon's config registry (`blizzard.hub.config_keys`,
`blizzard.runner.config_keys`), so the merge gate's `contract drift` step turns red when a registry changes without a
regenerated document. `blizzard:config-contract` (`uv run pytest tests/test_config_contract.py`) holds each committed
document to its registry. A test run reads config only through those registries: an undeclared key read raises.

Regenerate after an intended change:

```bash
mise run export-contracts
```
