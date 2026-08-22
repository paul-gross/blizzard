# Deriving events from transcripts — the analytics lane

Alongside the [raw transcript segments](./transcripts.md), the hub maintains a derived, queryable **event stream**: one
row per interesting occurrence a segment's turns hold — today, a file read naming a concrete path, a skill invocation,
or an agent spawn — stamped with the node-step context the segment already carries (chunk, node, epoch, spawn
generation), tagged with sidechain nesting depth and the nearest-enclosing agent type, and stamped with the extractor
version that produced it.

**Automatic, no configuration.** A standing in-process sweep derives every final, not-superseded segment's events on its
own interval; there is nothing to enable and nothing in `[transcripts]` gates it — it runs whenever segments exist to
derive, independent of `ship`. A segment's events typically appear within one sweep interval of the segment landing,
with no operator action.

**It is a projection, not a second source of truth.** Every row is a pure, immutable computation over the segment's own
stored content — never itself authoritative, and never gating a claim, a spawn, or any admission decision. Dropping the
whole table costs only the time to re-derive it, which the sweep does on its own the next time it runs; nothing else
needs to change and no downtime is required. If a segment's stored content later changes underneath an earlier
derivation (a rejected record accepted, a late record landing), the sweep detects it and re-derives that segment on its
next pass.

**Forcing it sooner: `blizzard hub analytics re-derive`.** The standing sweep is usually enough, but an operator who
wants a specific segment's events immediately, or who bumped the extractor version and wants a chunk (or the whole
fleet) re-derived now rather than waiting for the sweep's own cadence, can force it:

```text
blizzard hub analytics re-derive --segment SEGMENT_ID
blizzard hub analytics re-derive --chunk CHUNK_ID [--limit N]
blizzard hub analytics re-derive [--limit N]
```

`--segment` forces that one segment regardless of whether it currently looks up to date. `--chunk` (or neither option,
for the whole fleet) derives up to `--limit` (default 50) of that scope's current candidates and reports how many remain
— a nonzero `remaining` means calling it again continues from where it left off, the same way the standing sweep itself
converges. **No downtime**: it runs the same in-process reconciler already live inside the hub, driven directly rather
than waiting for its next tick — never a `--dir` verb, and the hub is never stopped for it. It requires `admin`+
(`analytics:admin`), since it mutates, unlike the read-only `transcript:read` grant the
[segment routes](./transcripts.md) are gated on.

## Reading events and counts — `blizzard hub analytics events`/`summary` (blizzard#255, #257)

Two operator verbs read the derived event stream above — `blizzard hub analytics
events` for the raw per-occurrence
rows, `blizzard hub analytics summary` for four canned occurrence counts — thin wrappers over
`GET /api/analytics/events` and `GET
/api/analytics/counts/*`, gated on the same `transcript:read` grant the segment
routes above use:

```text
blizzard hub analytics events [FILTERS] [--cursor CURSOR] [--limit N] [--ndjson]
blizzard hub analytics summary counts-files       [FILTERS]
blizzard hub analytics summary counts-skills      [FILTERS]
blizzard hub analytics summary counts-agent-types [FILTERS]
blizzard hub analytics summary counts-nodes       [FILTERS]
```

`FILTERS` is the shared vocabulary every event-projection route understands: `--graph`, `--source`, `--since`, `--until`
(the four fields every analytics dataset in this doc shares), `--extractor-version` (defaults to the version the
standing sweep currently derives; naming an older one reads that version's own rows rather than mixing them, since
mixing would double-count the same occurrence), and the four event-shape filters `--kind`, `--tool`, `--subject-prefix`,
`--node`. Each `summary` counts dataset only takes the subset its own route exposes — `counts-files` skips `--kind`
(fixed to `file_read`), `counts-skills` takes only `--extractor-version`/`--node` of the six (a skill name is already
flat, so a prefix or tool narrows nothing), and `counts-nodes` skips `--node` (naming one would select a single group
rather than narrow the count). A flag a chosen dataset does not expose is refused with a `does not apply to dataset`
error, never silently dropped.

`events` has three output modes: a human table by default; `--json`, which prints the raw single-page envelope including
its `next_cursor` — the same page `GET
/api/analytics/events` itself returns; and `--ndjson`, which streams every
matching event from `GET /api/analytics/events/ndjson` to stdout, one JSON object per line, unbounded and unpaged —
incompatible with `--json`, `--cursor`, and `--limit`, since the streaming route takes neither. `--since`/`--until` are
read in the operator's own local wall clock and converted to UTC before crossing the wire, not merely relabeled — a bare
`--since 10:00` means 10am the operator's own clock, whatever timezone the hub process itself runs in.

A bare 401 gets the `blizzard hub login` hint; a bare 403 (missing `transcript:read`, or a runner token presented on an
operator verb) surfaces the API's own `detail` message unchanged, rather than a generic refusal.

## The operational datasets — durations, spend, outcomes (blizzard#256)

Alongside the derived event stream, the same namespace exports the fleet's operational numbers as read-shaped datasets:
step durations, tokens and spend, and node failure/retry outcomes — the numbers already computable from board-serving
reads, reshaped for bulk export rather than a per-chunk detail view. No new facts are stored; every dataset is derived
at read time from primary sources — `transitions`, `lease_facts`, `usage_facts`, `chunk_bounces`, and `chunk_migrations`
— joined against `chunks`, `graphs`, `graph_nodes`, and `chunk_work_refs` for graph/source resolution.
`blizzard hub analytics summary` reads all six, one dataset per call:

```text
blizzard hub analytics summary durations-nodes  [FILTERS]
blizzard hub analytics summary durations-graphs [FILTERS]
blizzard hub analytics summary spend-nodes      [FILTERS]
blizzard hub analytics summary spend-graphs     [FILTERS]
blizzard hub analytics summary spend-chunks     [FILTERS] [--cursor CURSOR] [--limit N] [--ndjson]
blizzard hub analytics summary outcomes-nodes   [FILTERS]
```

Every dataset here takes only the shared `--graph`/`--source`/`--since`/`--until` filters the events/counts datasets
above also share — no `--kind`/`--tool`/ `--subject-prefix`/`--node`, since this family groups over
`transitions`/`usage_facts`/ etc., not the event projection — and the same `transcript:read` gate, strictly narrower
than the `fleet:view` gate the same spend numbers already sit behind on the board. What `--graph` narrows *by* differs
per dataset, though, so two datasets' numbers for one graph are not directly comparable for a chunk that has migrated:
durations and outcomes' judged half filter by the transition's own graph, spend filters by the chunk's *current* graph
pin, and outcomes' failure half uses the failed attempt's own derived graph — each dataset's own wire model states its
resolution; this is not one shared meaning. `spend-chunks` is the one dataset that pages or streams:
`--cursor`/`--limit` (default 200) page the human/`--json` output, and `--ndjson` streams the whole filtered set from
`GET /api/analytics/spend/chunks/ndjson` to stdout instead — the same `--json`/`--cursor`/`--limit` incompatibility
`events`' own `--ndjson` carries, since the streaming route takes neither either. Per-node and per-graph groupings
return one JSON envelope each, with no cursor of their own — `node_id`/`graph_id` are per-graph-*version* ids, so the
*ceiling* on either envelope's size grows every time a graph is minted, whatever the fleet has or hasn't run; the size a
caller actually receives does not, since a grouping only emits keys with matching activity — an unrun graph mint adds
zero rows to what ships. Unlike `events`' own NDJSON export, the chunk-spend one is not a point-in-time snapshot: each
page's sums are recomputed at page-fetch time, so a chunk emitted on an early page can be invalidated by a usage fact
recorded while a later page is still streaming. Field-by-field shapes are the committed `openapi/hub.openapi.json`'s own
record — the wire models that generate it are each dataset's one prose home, not restated here.

Seven callouts worth an operator's attention rather than a field's own doc comment:

- A duration is hub-observed wall-clock time, not runner-measured — see the `AnalyticsDurationView` wire model
  (`openapi/hub.openapi.json`) for the parked-gate vs. delayed-flush directions this doesn't restate here.
- Durations and the judged-choice distribution cover a step completed by an ordinary transition only — a hub-executed
  node's own exit transition has no measurable wall-clock interval of its own, so durations excludes it, and a step
  completed via a cross-graph migration (an authored edge, an intent, or follow-latest — the transition-borne sources)
  is invisible to both, since neither reads `chunk_migrations`. A documented gap, not a silent one.
- Outcomes reports a judged-choice distribution and a retry-consuming attempt-failure count as two separate numbers,
  never one blended failure rate — a delivery kick-back counts as neither, including the kick-back's own routing
  transition, which shares the bounce's epoch and is excluded from the judged count for exactly that reason.
- Outcomes' two counts window on different instants (the judging transition's own vs. the failed attempt's lease mint),
  so a boundary case can count on one side only.
- None of the seven routes defaults or requires a `since`/`until` window — unlike `/api/spend` (which requires `since`)
  or `/api/activity` (which defaults to 24h) over overlapping fact tables. A deliberate, still-open deferral: an
  unfiltered call costs a full scan of whatever it reads, acceptable at today's real fleet volumes but not bounded by
  anything this namespace enforces.
- The chunk-spend NDJSON export (`spend/chunks/ndjson`) pages through `usage_facts` — the hub's highest-cardinality fact
  table, with no index on `chunk_id` — 500 rows at a time, re-scanning the full table on every page. Exporting the
  fleet's whole per-chunk spend costs `ceil(N/500)` such scans; a deliberate deferral alongside the unbounded window
  above, not yet paired with an additive index.
- Every analytics read shares the hub's default connection pool (5 + 10 overflow) with the fleet's own write path — no
  dedicated budget is carved out. A burst of concurrent unfiltered analytics calls can hold enough connections to make
  an unrelated write wait out the pool's checkout timeout; a deliberate, undeclared deferral, not a magnitude this
  namespace bounds today.
