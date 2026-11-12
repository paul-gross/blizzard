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

Each key also states its `lifetime` — when a change to its value takes effect — as one of four values: `pass` (read at
the start of each pass of the loop that uses it, so the next pass takes a change), `session` (handed to a worker when
its session launches, so a running session keeps what it launched with), `boot` (read when the daemon starts, so a
change takes a restart), or `immutable` (written once, when the thing it belongs to is created). Every key holding a
value states one, an `open table` included; a `table` or `array of tables` only groups its children and states `null`. A
deprecated key states its replacement's lifetime. Moving a key's lifetime earlier is additive; moving it later is a
break. A key's `env` names the environment variable that supplies its value and overrides the file, and only a `boot`
key states one; it is `null` otherwise. It is distinct from the document's top-level `env`, the token env file.

A deprecated key's `replaced_by` names one of three homes: another key of the same document, a key of the other daemon's
file as `<path> in <file>.toml` (such as `tracing.worker_programs in blizzard-host.toml`), or a record kind configured
through the API rather than the file (such as `work source records`).

A key of type `open table` has free-form children, such as `flags`, `hub_tokens` or an alias table; no key beneath it is
listed. A deprecated key is still read — to refuse it, graduate it, or honor it — so it stays declared.

`blizzard-export-contracts` builds the documents from each daemon's config registry (`blizzard.hub.config_keys`,
`blizzard.runner.config_keys`), so the merge gate's `contract drift` step turns red when a registry changes without a
regenerated document. `blizzard:config-contract` (`uv run pytest tests/guards/test_config_contract.py`) holds each
committed document to its registry. A test run reads config only through those registries: an undeclared key read
raises.

Regenerate after an intended change:

```bash
mise run export-contracts
```
