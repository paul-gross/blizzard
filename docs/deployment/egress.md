# Fact egress

The hub can write its operational facts to a directory of immutable files, so a warehouse, a notebook or DuckDB can
answer cost by node by day, or the slowest station of the week, without calling the hub. Two datasets leave: `steps`,
one row per closed node-step, and `invocations`, one row per usage report. This is a push export to files you own. The
pull-based HTTP exports are in [`analytics.md`](./analytics.md).

## Turning it on

The export runs when `[egress] directory` is set in `blizzard-hub.toml`, and not otherwise: with it unset no sweep
starts and the hub behaves as before. `format` chooses `ndjson` or `parquet`. The `[egress]` block of the generated
config template owns every other key and its default.

```toml
[egress]
directory = "/var/lib/blizzard-export"
format = "ndjson"
```

A directory that does not exist, or that the hub cannot write, fails the first pass rather than startup, and the hub
records an `egress-write-failed` event. The export stops writing, and records the shortfall, when the directory's
filesystem has less than `min_free_bytes` free, so it cannot fill the disk the hub's own store may share.

## Formats

- **`ndjson`** is the default and needs nothing extra. A file is gzip-compressed, `.ndjson.gz`, with one JSON object per
  row in column order. Times are RFC 3339 strings with a `Z` suffix, money is a string at scale nine so no reader rounds
  it through a float, and lists are JSON arrays.
- **`parquet`** needs `pyarrow`, which only the `blizzard[egress]` install extra brings. A file is zstd-compressed with
  its schema inside it: times are `timestamp[us, UTC]`, money is `decimal(18, 9)` and lists are `list<string>`.

One export writes one format. A hub configured for Parquet without the extra still starts: the export stays off, the hub
records one `egress-config-rejected` event naming the missing extra, and `egress status` reports it.

## The data dictionary

Generated from [`contracts/egress/`](../../contracts/egress/README.md), which is the versioned contract the files
follow. A value marked `open` is not exhaustive: a consumer must tolerate a value it does not list, which is how a new
value stays additive. A null in a nullable column means what its meaning says.

<!-- egress-dictionary:begin -->

### `steps`

Major version 1; identity column `step_key`; partitioned by the UTC date of `ended_at`.

| Column                | Type           | Null | Meaning                                                                                | Values                                                                                                                             |
| --------------------- | -------------- | ---- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `step_key`            | `string`       | no   | The row's identity: chunk_id/epoch, or chunk_id/epoch/gate/decision_id for a gate step |                                                                                                                                    |
| `trace_id`            | `string`       | no   | The step's derived trace id, as 32 hex characters, so a row joins to its trace         |                                                                                                                                    |
| `step_kind`           | `string`       | no   | runner, hub or gate                                                                    | closed: `runner`, `hub`, `gate`                                                                                                    |
| `chunk_id`            | `string`       | no   | The chunk                                                                              |                                                                                                                                    |
| `work_refs`           | `list<string>` | no   | Its work items as source-native tokens; empty when it has none                         |                                                                                                                                    |
| `sources`             | `list<string>` | no   | The distinct work sources of those items                                               |                                                                                                                                    |
| `graph_id`            | `string`       | no   | The graph the step stood in                                                            |                                                                                                                                    |
| `graph_name`          | `string`       | no   | The graph's name                                                                       |                                                                                                                                    |
| `node_id`             | `string`       | no   | The node                                                                               |                                                                                                                                    |
| `node_name`           | `string`       | no   | The node's name                                                                        |                                                                                                                                    |
| `epoch`               | `int64`        | no   | The epoch                                                                              |                                                                                                                                    |
| `decision_id`         | `string`       | yes  | A gate step's decision; null otherwise                                                 |                                                                                                                                    |
| `visit`               | `int64`        | no   | Which arrival at this node this is, counting from 1                                    |                                                                                                                                    |
| `runner_id`           | `string`       | yes  | The runner that held a runner step; null for hub and gate steps                        |                                                                                                                                    |
| `harness_id`          | `string`       | yes  | The harness of the step's last invocation                                              |                                                                                                                                    |
| `models`              | `list<string>` | no   | Distinct models across its invocations                                                 |                                                                                                                                    |
| `started_at`          | `timestamp`    | no   | When the step started                                                                  |                                                                                                                                    |
| `ended_at`            | `timestamp`    | no   | When the step ended; a gate ends at resolved_at when resolved                          |                                                                                                                                    |
| `closed_at`           | `timestamp`    | no   | When the closing fact was recorded; equal to ended_at except for a resolved gate       |                                                                                                                                    |
| `duration_ms`         | `int64`        | no   | ended_at minus started_at                                                              |                                                                                                                                    |
| `outcome`             | `string`       | no   | How the step ended                                                                     | closed: `transitioned`, `gated`, `migrated`, `escalated`, `released`, `stopped`, `completed`, `superseded`, `decided`, `restarted` |
| `choice`              | `string`       | yes  | The resolved choice, when there is one                                                 |                                                                                                                                    |
| `to_node_name`        | `string`       | yes  | Where it led: a node name, done, or graph:<name>                                       |                                                                                                                                    |
| `preceded_by`         | `string`       | yes  | restart, requeue or released-claim                                                     | closed: `restart`, `requeue`, `released-claim`                                                                                     |
| `bounce_cause`        | `string`       | yes  | conflict, checks, master-moved, poll-timeout, or a choice                              | open: `conflict`, `checks`, `master-moved`, `poll-timeout`                                                                         |
| `asks`                | `int64`        | no   | Asks raised in the step                                                                |                                                                                                                                    |
| `asks_unanswered`     | `int64`        | no   | How many asks were never answered                                                      |                                                                                                                                    |
| `wait_queue_ms`       | `int64`        | no   | Queue wait, summed                                                                     |                                                                                                                                    |
| `wait_claim_ms`       | `int64`        | no   | Claim wait, summed                                                                     |                                                                                                                                    |
| `wait_ask_ms`         | `int64`        | no   | Ask wait, summed                                                                       |                                                                                                                                    |
| `wait_pause_ms`       | `int64`        | no   | Pause wait, summed                                                                     |                                                                                                                                    |
| `wait_pickup_ms`      | `int64`        | no   | Decision pickup wait, summed                                                           |                                                                                                                                    |
| `invocations`         | `int64`        | no   | How many invocations rows belong to the step                                           |                                                                                                                                    |
| `input_tokens`        | `int64`        | no   | Summed across its invocations, with blizzard's uncached input count                    |                                                                                                                                    |
| `output_tokens`       | `int64`        | no   | Summed across its invocations                                                          |                                                                                                                                    |
| `cache_read_tokens`   | `int64`        | no   | Summed across its invocations                                                          |                                                                                                                                    |
| `cache_create_tokens` | `int64`        | no   | Summed across its invocations                                                          |                                                                                                                                    |
| `cost_billed_usd`     | `money`        | yes  | The harness-billed cost, summed; null when no invocation carried one                   |                                                                                                                                    |
| `cost_estimated_usd`  | `money`        | yes  | The estimated cost, summed; null when no invocation carried one                        |                                                                                                                                    |
| `cost_partial`        | `bool`         | no   | Some invocation carried neither a billed nor an estimated cost                         |                                                                                                                                    |
| `billed_partial`      | `bool`         | no   | Some invocation carried no billed cost                                                 |                                                                                                                                    |
| `exported_at`         | `timestamp`    | no   | When this copy of the row was written                                                  |                                                                                                                                    |

Newest copy of each `step_key`:

```sql
SELECT *
FROM (
  SELECT s.*, row_number() OVER (PARTITION BY step_key ORDER BY exported_at DESC) AS copy_rank
  FROM steps AS s
) AS ranked
WHERE copy_rank = 1
```

### `invocations`

Major version 1; identity column `usage_id`; partitioned by the UTC date of `recorded_at`.

| Column                | Type        | Null | Meaning                                           | Values                           |
| --------------------- | ----------- | ---- | ------------------------------------------------- | -------------------------------- |
| `usage_id`            | `int64`     | no   | The row's identity: the usage_facts id            |                                  |
| `step_key`            | `string`    | no   | The runner step it belongs to, by chunk and epoch |                                  |
| `trace_id`            | `string`    | no   | The step's derived trace id, as 32 hex characters |                                  |
| `chunk_id`            | `string`    | no   | The chunk                                         |                                  |
| `epoch`               | `int64`     | no   | The epoch                                         |                                  |
| `graph_id`            | `string`    | no   | The graph its step stood in                       |                                  |
| `graph_name`          | `string`    | no   | The graph's name                                  |                                  |
| `node_id`             | `string`    | no   | The node                                          |                                  |
| `node_name`           | `string`    | no   | The node's name                                   |                                  |
| `runner_id`           | `string`    | no   | The runner that reported it                       |                                  |
| `kind`                | `string`    | no   | spawn, resume or judge; a nudge reads resume      | open: `spawn`, `resume`, `judge` |
| `model`               | `string`    | no   | As reported                                       |                                  |
| `harness_id`          | `string`    | yes  | As reported                                       |                                  |
| `harness_version`     | `string`    | yes  | As reported                                       |                                  |
| `input_tokens`        | `int64`     | no   | As reported                                       |                                  |
| `output_tokens`       | `int64`     | no   | As reported                                       |                                  |
| `cache_read_tokens`   | `int64`     | no   | As reported                                       |                                  |
| `cache_create_tokens` | `int64`     | no   | As reported                                       |                                  |
| `cost_billed_usd`     | `money`     | yes  | As reported; null when absent                     |                                  |
| `cost_estimated_usd`  | `money`     | yes  | As reported; null when absent                     |                                  |
| `recorded_at`         | `timestamp` | no   | When the hub received it                          |                                  |
| `exported_at`         | `timestamp` | no   | When this copy of the row was written             |                                  |

Newest copy of each `usage_id`:

```sql
SELECT *
FROM (
  SELECT i.*, row_number() OVER (PARTITION BY usage_id ORDER BY exported_at DESC) AS copy_rank
  FROM invocations AS i
) AS ranked
WHERE copy_rank = 1
```

<!-- egress-dictionary:end -->

## The directory

```text
<directory>/
  steps/v1/date=2026-10-01/steps-20261001T061500Z-k7q2-000042.ndjson.gz
  invocations/v1/date=2026-10-01/invocations-20261001T061500Z-k7q2-000042.ndjson.gz
  _manifests/20261001T061500Z-k7q2-000042.json
  _schema/steps.v1.json
  _schema/invocations.v1.json
  .staging/
```

- **Partitions.** Each dataset has its own tree under its major version, partitioned by the UTC date of the row's own
  time. A backfilled row lands in its own date's partition, in a new file.
- **Files are immutable.** A file is staged under `.staging/`, then placed under its final name by an operation that
  fails rather than replace. A reader never sees a half-written file, and blizzard never deletes or overwrites anything
  in the directory: pruning it is yours.
- **Manifests.** A pass writes its manifest last. It lists every file the pass placed, with dataset, version, partition,
  row count, first and last cursor position and SHA-256.
- **Schemas.** `_schema/` holds each dataset's columns, types, nullability and meanings, which are the same as the
  dictionary above. NDJSON carries no schema of its own, so this is its schema.

### Loading whole passes

Load only the files a manifest names. A manifest exists only after every file of its pass is in place, so a loader that
starts from `_manifests/` never reads a partial pass, and it can verify each file against the listed `sha256`. Remember
the manifests already loaded; a manifest name sorts in the order the passes ran.

## Keeping the newest copy of each row

Delivery is at least once. A hub killed between placing a file and advancing its cursor writes the same rows again, a
backfill repeats rows on purpose, and usage that lands after its step was exported writes the step again with corrected
totals. Copies of one identity are identical or newer, so a reader keeps the copy with the latest `exported_at`. The
dictionary above carries that view for each dataset; every recipe below runs it unchanged over a relation named after
the dataset.

## Recipes

### DuckDB

Read the directory in place. Bind the dataset's name to the files, then run the dictionary's view over it. For NDJSON:

```sql
CREATE VIEW steps AS
SELECT * FROM read_json_auto('<directory>/steps/v1/*/*.ndjson.gz', format = 'newline_delimited');
```

For Parquet, `SELECT * FROM read_parquet('<directory>/steps/v1/*/*.parquet')`. Wrap the dictionary's newest-copy view
around that relation. Both recipes below read that view as `steps_newest`, and a station is a graph and a node name.

Cost by node by day, billed and estimated kept apart as the spend surface keeps them. NDJSON carries money as a string,
so the recipe reads it as a decimal; Parquet already is one:

<!-- recipe:cost-by-node-by-day -->

```sql
SELECT graph_name, node_name, CAST(ended_at AS DATE) AS day,
       sum(CAST(cost_billed_usd AS DECIMAL(18, 9))) AS billed_usd,
       sum(CAST(cost_estimated_usd AS DECIMAL(18, 9))) AS estimated_usd
FROM steps_newest
GROUP BY graph_name, node_name, day
ORDER BY day, graph_name, node_name
```

The slowest station of the week, by mean step duration over the steps that ended in the last seven days, gates included:

<!-- recipe:slowest-station-of-the-week -->

```sql
SELECT graph_name, node_name, avg(duration_ms) AS mean_duration_ms
FROM steps_newest
WHERE ended_at >= now() - INTERVAL 7 DAY
GROUP BY graph_name, node_name
ORDER BY mean_duration_ms DESC
LIMIT 1
```

### Object storage with `rclone`

Copy the directory to a bucket and let the loader on the other side read manifests first. Copy `_manifests/` last, so
the manifest of a pass reaches the bucket only after the files it lists:

```bash
rclone copy <directory> remote:bucket/blizzard --exclude '.staging/**' --exclude '_manifests/**'
rclone copy <directory>/_manifests remote:bucket/blizzard/_manifests
```

### A warehouse loader

A stock loader that ingests NDJSON or Parquet from a bucket takes the file list from a manifest, loads each dataset's
files into a table of its name, and applies the dictionary's view. Use the types in the dictionary, and read money as a
decimal with scale nine.

## Backfill and reset

`blizzard hub egress status` reports whether the export is on, its directory and format, each dataset's cursor and lag,
the last pass and file, the last error and the free space. Run it first, and again after either verb below.

- **Backfill** writes the rows of a window again without moving a cursor:
  `blizzard hub egress backfill --since <t>
  --until <t>`, narrowed with `--dataset`. The rows are assembled as live
  ones are, from the record as it stands, into new files with `backfill` in their names. `--dry-run` counts and writes
  nothing, and a window over `backfill_max_window` is refused.
- **Reset** moves one dataset's cursor: `blizzard hub egress reset --dataset <name> --to <t>`. Forward skips the window
  and never exports it; back repeats it into new files. The move is recorded as an `egress-cursor-reset` event. When an
  export is turned off and on again its cursor resumes where it stopped, so the directory holds no gap unless you reset.

The verbs' flags are in `--help`.

## What never leaves

No row carries:

- prompt text, transcript content or check output
- an ask's question or answer, or a gate's choice descriptions
- a bounce's envelope
- the name or login of anyone who resolved a gate or answered an ask
- a chunk's or work item's title or body
- an escalation's takeover command

Ids, names, counts, times and costs are the whole of what leaves.

## Changes to the shape

[`versioning.md`](../versioning.md#the-egress-contract) owns what may change and how a breaking change is announced.
