# OpenCode 2.2.6 supplements

This directory carries no `manifest.json`, so it is not an admitted corpus: each file pins the facts one OpenCode 2
reader takes from 2.2.6.

## Price catalog

What OpenCode 2.2.6 keeps in its own store for pricing a model, which the OpenCode 2 price catalog reads.

- `models_catalog_row.json` — the `value` of the `kv` row `models-dev:v2:catalog`, trimmed to five models, one per case:
  a `context.gt` override (`openai/gpt-6.1-sol`), no overrides (`qiniu-ai` has two context steps; `opper` has none), a
  map-valued cache-write rate (`opper/claude-sonnet-5`), a `service_tier` override
  (`amazon-bedrock/mistral.mistral-large-3-675b-instruct`), and a `context.gte` override (`xai/grok-4.5`). `updatedAt`
  and `digest` are verbatim; the digest no longer matches the trimmed `body`.

Version-bound facts:

- The store is SQLite at `$XDG_DATA_HOME/opencode/opencode.db` (default `~/.local/share/opencode/opencode.db`); its `kv`
  table has the columns `key`, `value`, `time_created`, `time_updated`.
- The catalog row's key is `models-dev:v2:catalog` for the default catalog URL. A non-default `OPENCODE_MODELS_URL`
  stores it under `models-dev:v2:catalog:<hash of the url>`; the hash is OpenCode's own, so blizzard reads only the
  default key. A `models-dev:v2:catalog:meta` row sits beside it and is not read.
- `value` is JSON `{updatedAt, digest, body}`, and `body` is itself a JSON-encoded string keyed `provider` → `models` →
  model id → `cost`.
- `cost.text.input` and `cost.text.output` each hold `price` (dollars per 1,000,000 tokens) and optional `overrides`.
  `input` also holds `cache.read` and `cache.write`; an override's `cache` replaces them. A `cache.write` is a number,
  or a map keyed by TTL (`5m`, `1h`) on some Anthropic models.
- An override's `when` is a condition: `context` (`gt` or `gte`), `service_tier`, `utc_hours`, `date`, `any`, or `all`.
  The `context` size is the step's prompt size: input, cache read and cache write tokens.

## Execution observation

`execution_observation.json` holds the answers the execution observer reads for one lease's tree mid-tool: a root whose
newest step runs a `subagent` call and whose previous step completed a `read` call, and the child that call spawned,
running a `shell` call. It is keyed by what is asked:

- `info` — `GET /api/info`, which the binding checks against the registration.
- `active` — `GET /api/session/active` with both sessions running; `active_none` the same answer with none running.
- `children` — `GET /api/session?parentID=<id>`, per parent.
- `messages_newest_first` — `GET /api/session/<id>/message?order=desc&limit=20`, per session.
- `permission_requests` and `forms` — `GET /api/session/<id>/permission` and `/form`, per session, both empty.

It is **built, not captured**: no 2.2.6 service ran a turn for it. Every record keeps the field set and nesting the
2.1.4 corpus recorded for the same operation (`transcript_live.json` for a running tool part, `transcript_after.json`
for a completed one, `child_spawn_parts.json` for a `subagent` call, `child_sessions.json` for a child), with fresh ids,
times and text.

Version-bound facts it assumes:

- `session/active` answers `data` as an object keyed by session id; a session not in it is not running.
- A tool part's `state.status` is `running` until it ends and `completed` after; its `time.completed` (epoch
  milliseconds) is set when it ends, as an assistant message's `time.completed` is when its step ends.
- `order=desc` lists a session's messages newest first.
