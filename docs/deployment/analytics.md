# Analytics

The hub maintains a derived, queryable event stream over shipped transcript segments: one row per occurrence a segment's
turns hold — today file reads naming a concrete path, skill invocations, and agent spawns — stamped with node-step
context, sidechain depth, nearest-enclosing agent type, and extractor version. The same namespace exports the fleet's
operational numbers as read-shaped bulk datasets — step durations, tokens and spend, node failure/retry outcomes —
storing no new facts, each derived at read time from the primary fact tables.

These are read through the hub. To keep the event stream beyond what the hub holds, or to analyse it in a warehouse,
export it: [`egress.md`](./egress.md#events) writes the same events to files, with the views that read them.

## Derivation

Derivation is automatic: a standing in-process sweep derives every final, not-superseded segment on its own interval,
independent of `ship` and gated by nothing in `[transcripts]`; events typically appear within one sweep interval. The
stream is a projection, never a second source of truth: rows are pure computations over stored segment content, gating
no claim, spawn, or admission; dropping the table costs only the sweep's own re-derivation, and content changed under an
earlier derivation is re-derived next pass.

`blizzard hub analytics re-derive` forces derivation sooner: `--segment` forces one segment regardless of apparent
freshness, but only a visible one — a superseded, non-final, or unknown segment derives nothing and is reported as not
visible; `--chunk`, or neither flag for the fleet, derives up to `--limit` (default 50) candidates, reporting how many
remain — call again to continue. `re-derive` drives the hub's own live in-process reconciler — never a `--dir` verb, the
hub never stopped — and requires admin and above (`analytics:admin`) since it mutates.

## Reading the datasets

`blizzard hub analytics events` reads the raw per-occurrence rows, `analytics summary` canned counts — thin wrappers
over the `/api/analytics` routes, gated on the same `transcript:read` as the segment routes. `summary`'s seven
operational datasets (durations and spend by node and graph, spend by model, spend by chunk, outcomes by node) take the
shared four filters plus `--model`, `--harness-id` and `--harness-version` — they group over transitions and usage
facts, not the event projection, and only a usage fact records those three — and the same `transcript:read` gate,
strictly narrower than the board's `fleet:view` gate over the same spend numbers. `--effort` stays events-only:
`usage_facts` carries no effort, so an operational dataset refuses it. `spend-models` groups spend by the model each
usage fact recorded, one row per model, key ascending; it neither pages nor streams.

`events` outputs a human table, `--json` (the raw single-page envelope with its `next_cursor`), or `--ndjson` (streaming
every match unbounded and unpaged); `--ndjson` is incompatible with `--json`, `--cursor`, and `--limit`, which the
streaming route does not take. `spend-chunks` is the one operational dataset that pages or streams: `--cursor`/`--limit`
(default 200) page the human and `--json` output, `--ndjson` streams the whole filtered set, with the same
incompatibility as `events`' own. `counts-files --limit N` is a separate opt-in: it returns the top N paths by count
(ties by path), each with its complete count, and the human output says other paths may be omitted. It takes no cursor
and does not stream; without `--limit` every path returns.

Each `summary` dataset takes only the filter subset its route exposes — the verb's `--help` is the contract — and an
inapplicable flag is refused with a does-not-apply error, never silently dropped.

## Filters

Every dataset shares `--graph`, `--source` (a work source's id or slug, or `hub`), `--since`, `--until`;
`--extractor-version` defaults to the sweep's current version — an older one reads that version's rows alone, since
mixing would double-count — and the event projection adds `--kind`, `--tool`, `--subject-prefix`, `--node`.
`--harness-id`, `--harness-version` and `--model` narrow the counts and the operational datasets alike
([how](#interpreting-the-numbers)); `--effort` narrows the counts alone. `--since`/`--until` are read in the operator's
local wall clock and converted to UTC before the wire — a bare 10:00 means the operator's 10am, whatever timezone the
hub runs in. A time ending `Z` or `±hh:mm` is that instant instead.

Every analytics read takes `?project=<id|slug>` to read one project's chunks; without it, it reads the whole tenant
([projects.md](./projects.md#--project-on-the-cli)).

None of the eight operational routes defaults or requires a since/until window (unlike `/api/spend`, requiring `since`,
or `/api/activity`, defaulting to 24h): an unfiltered call costs a full scan — acceptable at today's volumes, a
deliberate open deferral. Unfiltered durations, outcomes and sweeps reads are complete historical rollups on purpose,
and their cost grows with history; `counts/files?limit=N` is the one read that bounds the rows it returns.

## Interpreting the numbers

- What `--graph` narrows by differs per dataset — the transition's own graph for durations and outcomes' judged half,
  the chunk's current pin for spend, the failed attempt's derived graph for outcomes' failure half — so two datasets'
  numbers for one graph are not comparable for a migrated chunk; each wire model states its resolution.
- The provenance flags narrow by lease attempt. A durations, outcomes or spend row matches when its attempt
  `(chunk, epoch)` has at least one usage fact, judge facts included, matching every flag given; spend filters the usage
  facts directly. So an attempt that ran on two models counts under each, and per-model numbers do not partition the
  unfiltered total; an attempt with no usage fact — a crash before the first report, any hub-executed step — matches no
  filter; and a harness id or version recorded as unknown matches no value. Outcomes' failure half narrows only which
  attempts it counts: a failed attempt's node is still inferred from the chunk's whole movement history.
- Outcomes reports a judged-choice distribution and a retry-consuming attempt-failure count separately, never one
  blended failure rate; a delivery kick-back counts as neither, its routing transition sharing the bounce's epoch.
- Outcomes' two counts window on different instants — the judging transition versus the failed attempt's lease mint — so
  a boundary case can count on one side only.
- Durations and the judged-choice distribution cover only steps completed by ordinary transitions: a hub-executed node's
  exit has no measurable interval, and a cross-graph migration completion (authored edge, intent, or follow-latest) is
  invisible to both, neither reading `chunk_migrations` — a documented gap.
- A duration is hub-observed wall clock, not runner-measured; the `AnalyticsDurationView` wire model owns the
  parked-gate versus delayed-flush directions.
- Per-node and per-graph groupings return one envelope with no cursor; `node_id` and `graph_id` are per-graph-version
  ids, so the envelope-size ceiling grows with every mint while the received size does not — only keys with activity are
  emitted. Each node row names its graph and node and each graph row its graph, so a reader sees what an id is without a
  second lookup; the names are null where an id no longer resolves.

## Export costs

The chunk-spend NDJSON pages `usage_facts` — the highest-cardinality fact table, no `chunk_id` index — 500 rows a page,
re-scanning the full table each page: a whole-fleet export costs ceil(N/500) scans, a deferral not yet paired with an
additive index. And unlike `events`' NDJSON, the chunk-spend export is no point-in-time snapshot: each page's sums
recompute at fetch, so an early-page chunk can be invalidated by a usage fact recorded while a later page streams.

Analytics reads share the hub's default connection pool (5 plus 10 overflow) with the fleet's write path — no dedicated
budget — so a burst of unfiltered calls can make an unrelated write wait out the pool's checkout timeout.

## A routine run's own read

A running pass reads six of the same rollups a different way:
`blizzard runner analytics counts
{files,skills,agent-types,nodes} --since <instant> [--until <instant>]` and
`analytics spend {nodes,graphs} --since
<instant> [--until <instant>]` — the same rows the matching
`blizzard hub analytics summary` dataset serves for the same window, gated on the chunk's own run context rather than an
operator credential. `--since` is required here (unlike the operator's own optional one), so no worker call omits a
window — but that bounds the caller, not the query: `transcript_events` carries no index on `occurred_at` (only
`(extractor_version, id)`), so `counts/*` and `spend/*` still scan the full `extractor_version` partition regardless of
the window's width, the same full-scan deferral the operator's own routes carry (Filters, above). Neither verb takes
`--graph`, `--source`, or any of the event projection's own narrowing flags — the window is the only lever a routine run
gets. A chunk that is not a routine run 404s, the same refusal `blizzard runner garden findings` gives. It needs no hub
credential in its child environment either, is a pure client of the runner's local API authorized by the spawn-injected
lease identity, and shares [openapi/runner.openapi.json](../../openapi/runner.openapi.json) as its endpoint-shape home.

The node and graph verbs also take `--by-name` (the `by_name` query param, on the operator routes too). Ids split across
mints, so one node's use is spread over a row per mint; the flag folds them into one row per name. A node's `key` is
`<graph_name>/<node_name>`, since node names repeat across graphs, and a graph's is its `graph_name`; a row with no
resolvable name passes through under its id. A new runner against an older hub gets per-id rows with null names, since
the hub ignores the param; an older runner against a newer hub never sends it and drops the names.

## Errors and wire shapes

A bare 401 gets the `blizzard hub login` hint ([human-auth.md](./human-auth.md)); a bare 403 — missing
`transcript:read`, or a runner token on an operator verb — surfaces the API's own detail unchanged.

Field-by-field shapes are the committed [openapi/hub.openapi.json](../../openapi/hub.openapi.json)'s record — its
generating wire models are each dataset's one prose home.
