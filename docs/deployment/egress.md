# Fact egress

The hub can write its operational facts to a directory of immutable files, so a warehouse, a notebook or DuckDB can
answer cost by node by day, the slowest station of the week, or which files a station read, without calling the hub.
Three datasets leave: `steps`, one row per closed node-step, `invocations`, one row per usage report, and `events`, the
file reads, skill invocations and agent spawns derived from the fleet's transcripts. This is a push export to files you
own. The pull-based HTTP exports are in [`analytics.md`](./analytics.md), which is the place to look for a quick answer
rather than a persistent one.

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

View `steps_newest`:

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

View `invocations_newest`:

```sql
SELECT *
FROM (
  SELECT i.*, row_number() OVER (PARTITION BY usage_id ORDER BY exported_at DESC) AS copy_rank
  FROM invocations AS i
) AS ranked
WHERE copy_rank = 1
```

### `events`

Major version 1; identity columns `derivation_id`, `kind`, `turn_path`, `occurrence`; partitioned by the UTC date of `step_started_at`.

| Column              | Type        | On                | Null | Meaning                                                                                 | Values                                               |
| ------------------- | ----------- | ----------------- | ---- | --------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `record_type`       | `string`    | all               | no   | derivation, event or dropped                                                            | closed: `derivation`, `event`, `dropped`             |
| `segment_id`        | `string`    | all               | no   | The transcript segment                                                                  |                                                      |
| `extractor_version` | `string`    | derivation, event | yes  | The extractor that derived it; null on a dropped row                                    |                                                      |
| `derivation_id`     | `string`    | derivation, event | yes  | The derivation's identity, as 32 hex characters; null on a dropped row                  |                                                      |
| `derived_at`        | `timestamp` | derivation, event | yes  | When the hub derived it; null on a dropped row                                          |                                                      |
| `complete`          | `bool`      | derivation        | yes  | The marker's own flag: false when the derivation stopped short; derivation rows only    |                                                      |
| `event_count`       | `int64`     | derivation        | yes  | How many event rows the derivation holds; derivation rows only                          |                                                      |
| `dropped_at`        | `timestamp` | dropped           | yes  | When the hub dropped the segment's events; dropped rows only                            |                                                      |
| `kind`              | `string`    | event             | yes  | file_read, skill_invocation or agent_spawn; extensible; event rows only                 | open: `file_read`, `skill_invocation`, `agent_spawn` |
| `subject`           | `string`    | event             | yes  | The path, the skill name, or the spawned agent type; null when the extractor names none |                                                      |
| `tool`              | `string`    | event             | yes  | The tool the turn called                                                                |                                                      |
| `turn_path`         | `string`    | event             | yes  | The event's place in the segment; event rows only                                       |                                                      |
| `occurrence`        | `int64`     | event             | yes  | The event's place in the segment; event rows only                                       |                                                      |
| `occurred_at`       | `timestamp` | event             | yes  | The turn's own time, when the transcript carries one                                    |                                                      |
| `depth`             | `int64`     | event             | yes  | 0 for the main conversation, plus one per subagent nesting; event rows only             |                                                      |
| `agent_type`        | `string`    | event             | yes  | The nearest enclosing subagent's type; null at depth 0                                  |                                                      |
| `step_key`          | `string`    | all               | no   | The runner step the segment came from, by chunk and epoch                               |                                                      |
| `trace_id`          | `string`    | all               | no   | That step's derived trace id, as 32 hex characters                                      |                                                      |
| `step_started_at`   | `timestamp` | all               | no   | When that step started; the row's partition and backfill time                           |                                                      |
| `chunk_id`          | `string`    | all               | no   | The segment's chunk                                                                     |                                                      |
| `epoch`             | `int64`     | all               | no   | The segment's epoch                                                                     |                                                      |
| `spawn_generation`  | `int64`     | all               | no   | Which worker spawn on the lease produced the segment                                    |                                                      |
| `graph_id`          | `string`    | all               | no   | The graph the step stood in                                                             |                                                      |
| `graph_name`        | `string`    | all               | no   | The graph's name                                                                        |                                                      |
| `node_id`           | `string`    | all               | no   | The node                                                                                |                                                      |
| `node_name`         | `string`    | all               | no   | The node's name                                                                         |                                                      |
| `harness_id`        | `string`    | event             | yes  | As derived; event rows only                                                             |                                                      |
| `harness_version`   | `string`    | event             | yes  | As derived; event rows only                                                             |                                                      |
| `model`             | `string`    | event             | yes  | As derived; event rows only                                                             |                                                      |
| `effort`            | `string`    | event             | yes  | As derived; event rows only                                                             |                                                      |
| `exported_at`       | `timestamp` | all               | no   | When this copy of the row was written                                                   |                                                      |

View `events_current`:

```sql
SELECT *
FROM (
  SELECT m.*, row_number() OVER (PARTITION BY m.derivation_id, m.kind, m.turn_path, m.occurrence ORDER BY m.exported_at DESC) AS copy_rank
  FROM (
    SELECT
      e.*,
      max(CASE WHEN e.record_type = 'derivation' THEN e.derived_at END) OVER (PARTITION BY e.segment_id) AS newest_derived_at,
      max(CASE WHEN e.record_type = 'dropped' THEN e.dropped_at END) OVER (PARTITION BY e.segment_id) AS last_dropped_at
    FROM events AS e
  ) AS m
  WHERE m.record_type = 'event'
    AND m.derived_at = m.newest_derived_at
    AND (m.last_dropped_at IS NULL OR m.last_dropped_at <= m.newest_derived_at)
) AS ranked
WHERE copy_rank = 1
```

View `events_by_version`:

```sql
SELECT *
FROM (
  SELECT m.*, row_number() OVER (PARTITION BY m.derivation_id, m.kind, m.turn_path, m.occurrence ORDER BY m.exported_at DESC) AS copy_rank
  FROM (
    SELECT
      e.*,
      max(CASE WHEN e.record_type = 'derivation' THEN e.derived_at END) OVER (PARTITION BY e.segment_id, e.extractor_version) AS newest_derived_at,
      max(CASE WHEN e.record_type = 'dropped' THEN e.dropped_at END) OVER (PARTITION BY e.segment_id) AS last_dropped_at
    FROM events AS e
  ) AS m
  WHERE m.record_type = 'event'
    AND m.derived_at = m.newest_derived_at
    AND (m.last_dropped_at IS NULL OR m.last_dropped_at <= m.newest_derived_at)
) AS ranked
WHERE copy_rank = 1
```

<!-- egress-dictionary:end -->

## The directory

```text
<directory>/
  steps/v1/date=2026-10-01/steps-20261001T061500Z-k7q2-000042.ndjson.gz
  invocations/v1/date=2026-10-01/invocations-20261001T061500Z-k7q2-000042.ndjson.gz
  events/v1/date=2026-10-01/events-20261001T061500Z-k7q2-000042.ndjson.gz
  _manifests/20261001T061500Z-k7q2-000042.json
  _schema/steps.v1.json
  _schema/invocations.v1.json
  _schema/events.v1.json
  .staging/
```

- **Partitions.** Each dataset has its own tree under its major version, partitioned by the UTC date of the row's own
  time. A backfilled row lands in its own date's partition, in a new file. An `events` row's own time is the start of
  the step its transcript came from, so a re-derivation lands beside the copy it supersedes.
- **Files are immutable.** A file is staged under `.staging/`, then placed under its final name by an operation that
  fails rather than replace. A reader never sees a half-written file, and blizzard never deletes or overwrites anything
  in the directory: pruning it is yours.
- **Manifests.** A pass writes its manifest last. It lists every file the pass placed, with dataset, version, partition,
  row count, first and last cursor position and SHA-256. A manifest that holds `events` files also records the
  `extractor_version` the hub was on.
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

## Events

The `events` dataset answers questions about what a station's workers did: which files, which skills, which agents. It
reads the transcripts the runners ship, so it is only as complete as that shipping.

### Record types

One file holds three record types, told apart by `record_type`, so a derivation and its events land in the same file and
the same pass:

- `derivation`, one per exported derivation, even one that found no events.
- `event`, one per file read, skill invocation or agent spawn in that derivation.
- `dropped`, once when a segment stops counting, which voids every derivation of it under every extractor version.

The hub derives a segment's events as a whole, and the export writes a derivation whole. Re-deriving a segment writes a
new derivation beside the old one rather than editing it, so a reader can always tell which copy is the newest. The
`derivation` row is what makes an emptied segment visible: without it, a segment re-derived to nothing would leave its
older events standing.

### Reading current truth

An event counts when its derivation is the newest of its segment, whatever the extractor version, and no `dropped` row
of the segment is later than that derivation. The dictionary's `events_current` view says exactly this, and also keeps
one copy of an event delivered twice. A segment dropped and derived again counts again. `events_by_version` is the
second view: the newest derivation of each segment under each extractor version, for comparing one version against
another, filtered by `extractor_version`.

### Extractor versions

`[egress] extractor_versions` chooses whether the export writes only derivations under the hub's current extractor
version or every version's. After an upgrade the hub re-derives every segment, a batch per sweep, and each derivation is
exported when it lands, so the burst is bounded by `batch_limit` per pass. `events_current` rides through it untouched:
a segment the sweep has not reached keeps counting under its old version until its new derivation arrives, so totals do
not dip. Each manifest records the hub's extractor version; compare it with the versions present in `events_current` to
see how far the re-derivation has got.

### File paths

A file read's path is stored as the tool was given it, usually absolute, naming the machine's user and the worktree's
location. `[egress] file_paths` decides what leaves as the event's `subject`: the path relative to the worker's working
directory, a keyed hash, the path as stored, or nothing. A hashed or relative setting needs a key, read from the
environment variable `[egress] path_key_env` names. When it is unset or empty the hub refuses only the `events` dataset
and keeps exporting the others, and `egress status` says why.

The working directory itself never leaves, but even a relative path names the repository's layout. Give the export
directory the same care as the `transcript:read` permission.

### Backfill

A backfill of `events` selects by the start of the step the segment came from, so a window names the work done in it
rather than when the hub last derived it.

## Recipes

### DuckDB

Read the directory in place, through its manifests. A manifest lists the files its pass placed, so a file no manifest
names, such as a stray copy or a half-finished upload, is never read. Set each dataset's file list from `_manifests/`,
bind the dataset's name to those files, then run the dictionary's view over it. Replace `<directory>` with the export
directory and `<dataset>` with `steps`, `invocations` or `events`; run the first statement of a pair, then the second.

For NDJSON:

<!-- recipe:load-ndjson -->

```sql
SET VARIABLE <dataset>_files = (
  SELECT list('<directory>/' || file.path)
  FROM (SELECT unnest(files) AS file FROM read_json_auto('<directory>/_manifests/*.json', union_by_name = true))
  WHERE file.dataset = '<dataset>'
);
CREATE VIEW <dataset> AS
SELECT * FROM read_json_auto(getvariable('<dataset>_files'), format = 'newline_delimited');
```

`events` also needs `union_by_name = true` and `sample_size = -1`, since a column that is null throughout one file is
typed by another:

<!-- recipe:load-events-ndjson -->

```sql
SET VARIABLE events_files = (
  SELECT list('<directory>/' || file.path)
  FROM (SELECT unnest(files) AS file FROM read_json_auto('<directory>/_manifests/*.json', union_by_name = true))
  WHERE file.dataset = 'events'
);
CREATE VIEW events AS
SELECT * FROM read_json_auto(getvariable('events_files'), format = 'newline_delimited',
                             union_by_name = true, sample_size = -1);
```

For Parquet, the same file list, read with `read_parquet`:

<!-- recipe:load-parquet -->

```sql
SET VARIABLE <dataset>_files = (
  SELECT list('<directory>/' || file.path)
  FROM (SELECT unnest(files) AS file FROM read_json_auto('<directory>/_manifests/*.json', union_by_name = true))
  WHERE file.dataset = '<dataset>'
);
CREATE VIEW <dataset> AS SELECT * FROM read_parquet(getvariable('<dataset>_files'));
```

Wrap the dictionary's view around that relation. The cost and slowest recipes read the newest-copy view as
`steps_newest`, the events recipes read `events_current` the same way, and a station is a graph and a node name.

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

The files a station read in the last seven days, most read first. A path is relative to the worker's working directory
under the default `file_paths`:

<!-- recipe:files-a-station-read-last-week -->

```sql
SELECT graph_name, node_name, subject AS path, count(*) AS reads
FROM events_current
WHERE kind = 'file_read'
  AND step_started_at >= now() - INTERVAL 7 DAY
GROUP BY graph_name, node_name, subject
ORDER BY graph_name, node_name, reads DESC, path
```

How often each skill is invoked, by station:

<!-- recipe:skills-by-station -->

```sql
SELECT graph_name, node_name, subject AS skill, count(*) AS invocations
FROM events_current
WHERE kind = 'skill_invocation'
GROUP BY graph_name, node_name, subject
ORDER BY graph_name, node_name, invocations DESC, skill
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

- **Backfill** writes the rows of a window again without moving a cursor, narrowed with `--dataset`:

  ```sh
  blizzard hub egress backfill --since <t> --until <t>
  ```

  A time is either naive, read in the caller's own local time, or a zoned RFC 3339 instant such as an export's own times
  (`2026-10-06T14:30:00.000000Z`, or the same instant as `2026-10-06T16:30:00+02:00`). The rows are assembled as live
  ones are, from the record as it stands, into new files with `backfill` in their names. A window over
  `backfill_max_window`, or one whose `--until` is in the future, is refused (the command refuses a future `--until`
  itself, before it sends). A window that reaches past a dataset's live cursor is written anyway, and the live export
  writes those rows again when its cursor gets there. `--dry-run` counts and writes nothing, and works with the export
  off, which is a cheap way to size a window; without it, a hub with the export off refuses the backfill.
- **Reset** moves one dataset's cursor: `blizzard hub egress reset --dataset <name> --to <t>`. Forward skips the window
  and never exports it; back repeats it into new files. The move is recorded as an `egress-cursor-reset` event. When an
  export is turned off and on again its cursor resumes where it stopped, so the directory holds no gap unless you reset.

The verbs' flags are in `--help`.

## What never leaves

No row carries:

- prompt text, transcript content or check output, and an event's raw payload, the tool input and output behind it
- an ask's question or answer, or a gate's choice descriptions
- a bounce's envelope
- the name or login of anyone who resolved a gate or answered an ask
- a chunk's or work item's title or body
- an escalation's takeover command

Ids, names, counts, times and costs are the whole of what leaves, plus an event's subject as `file_paths` allows.

## Changes to the shape

[`versioning.md`](../versioning.md#the-egress-contract) owns what may change and how a breaking change is announced.
