# Work sources

The hub reads every chunk's work item through a configured work source: a credentialed binding to one forge repo, stored
as a record and managed with `blizzard hub source`. Like a [repository](./repositories.md), each record has a slug you
type, a display name, and an id the hub mints. Work sources are independent of delivery's forge access: a
[repository](./repositories.md) record controls where a chunk's PR is opened and landed, a work source record controls
where its work item is read from, and each carries a stored [secret](./secrets.md) of its own.

A hub with zero work source records is fully operable: the built-in hub source is always seated, needs no configuration
or credential, and resolves hub-authored work items (`hub:<n>`); records are for external forge repos only, one per repo
to ingest from.

The hub reads a source's record on every call, so a created, edited, retired or re-enabled source, and a replaced
secret, takes effect on the next ingest, label sweep or closure with no restart. A `[[work_source]]` block left in
`blizzard-hub.toml` stops the hub from starting; `blizzard hub config import-legacy` carries the blocks into records,
and [install.md](./install.md#work-sources-and-forge-settings-are-records) owns that upgrade step.

## Work source records

```bash
printf '%s' "$TOKEN" | blizzard hub secret create gh-token
blizzard hub source create blizzard --provider github --locator paul-gross/blizzard --secret gh-token --annotate
blizzard hub source list
blizzard hub source get blizzard
```

- `slug` is the handle ingest tokens (`slug:ref`, `slug#ref`) and board pointer labels (`{slug}#{ref}`) use; `hub` is
  reserved for the built-in source. [Naming a work source](#naming-a-work-source) owns the three names.
- `locator` is the owner/name coordinate the source is pinned to; each (provider, locator) pair belongs to one record,
  since two records for one repo would let an item be ingested twice under two identities.
- `provider` names the adapter grammar; only `github` exists.
- `forge` is the [forge](./forges.md) the source reads from, by id or slug, and its kind must be the source's
  `provider`. The forge owns the API and web origins, so a GitHub Enterprise source names a forge whose `api_base` is
  the Enterprise host, and the item's browsable URL uses that forge's web origin. The board offers enabled forges of the
  selected provider's kind; version-2 configuration documents name one by slug.
- `api_base` and `web_base` are deprecated. A write that sends one is accepted with a warning and resolved to the forge
  with that origin, created if none exists; [forges.md](./forges.md#the-old-address-fields) owns the rules. A `web_base`
  is accepted only when it is the forge's web origin. Check stored addresses before the forge migration with
  [`config check-forges`](./repositories.md#checking-forge-addresses-before-the-forge-migration).
- `secret` names the stored secret holding the forge token, revealed only when the hub calls the forge.

`create <slug>` stores the source at revision 1, named `--name` (default: the slug). It is refused when another source
holds the slug as its current one or the slug is `hub`. Taking a slug another source left moves it to the new source.
Every later change moves the revision by one:

- `edit <source>` changes only the flags given: `--slug`, `--name`, `--provider`, `--locator`, `--secret`, `--forge`,
  `--api-base`, `--web-base`, and `--annotate` or `--no-annotate`. `--clear api-base`, `--clear web-base`, or
  `--clear secret` unsets a field, and `--clear secret` is refused for a provider that needs a credential, which
  includes `github`. An edit that leaves every field as it is writes nothing and keeps the revision.
- `retire <source>` hides the source from `list` unless `--include-retired` is given, and ingest naming it is refused
  with a 422. Items already ingested from it keep their labels, close and annotate through it. A retired source keeps
  its provider and locator, so `create` for another slug on the same locator is refused with a message naming the
  holder; `enable` the holder instead.
- `enable <source>` lifts the retirement, and is refused while the source's secret is retired.

`get`, `edit`, `retire` and `enable` take the source's id or its slug.

`edit`, `retire`, and `enable` take `--if-match <revision>` and are refused, naming the current revision, when the
source has moved on. `hub` is the built-in source: `list` shows it as built-in, and every attempt to change it is
refused. Every write is recorded in the [change log](./config-changes.md).

Writes need the `config:edit` permission and reads need `fleet:view`. The record fields are also served over
`GET /api/work-sources` and `GET /api/work-sources/{source}`, where `{source}` is an id or a slug, and the document
schema at `GET /api/config/schema/work-sources`. A body carries `slug` and `name`; `name` defaults to `slug` on create.
A source whose secret cannot be revealed is an error on every call through it, never an absent source.

The board's Admin page lists work sources, with each record's revision, last change, and history. On a desktop, a user
with `config:edit` can also create, edit, retire, and enable them there; an edit shows the fields that will change
before it saves. On a phone the page is read-only and names the `blizzard hub source` command that makes the change. The
built-in `hub` source is listed and cannot be changed. The form picks a matching enabled forge instead of editing its
API and web origins. Version-1 configuration documents still accept the deprecated `api_base` and `web_base`; version 2
uses `forge`.

## Naming a work source

A work source carries three names, each with one job, with the same rules as a
[repository's](./repositories.md#naming-a-repository):

| Name             | What it is                                                                                                                                                                                                                                                               |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `work_source_id` | `wsrc_` followed by a ULID. It never changes, and everything that refers to the source stores it, every chunk's work refs included                                                                                                                                       |
| `slug`           | The handle you type: in ingest tokens, CLI arguments, API paths, project links and configuration documents. 1 to 64 lowercase letters, digits and hyphens, a hyphen only between two letters or digits, and never `hub`. Unique in the tenant; two tenants may share one |
| `name`           | The display name: 1 to 128 characters with no leading or trailing whitespace, unique nowhere. Only ever shown, never looked up. It is the slug when `create` is not given one                                                                                            |

Wherever a work source is accepted, its id or its slug names it; a display name never does. An id names its source
whatever the slug is now.

A source's slug and name may change, and nothing that refers to the source moves: its chunks' work refs, its project
links, its pending closes and its change-log rows keep pointing at it. Every work ref is displayed under the source's
current slug, so after `edit acme --slug acme-core` the board, `chunk show` and every refusal render `acme-core#42` for
a ref ingested as `acme:42`. The slug a source leaves becomes its **former slug**: an ingest token, a URL, a script or a
project link written against it keeps reaching the source, until another source takes that slug. From then on it names
its new holder. The one place a former slug is refused is a configuration document, which answers `422` naming the
source that left it ([config-documents.md](./config-documents.md)). A configuration that must survive any slug change
names sources by id.

A hub upgraded from a release whose work sources were named by `name` carries each name forward, as it does a
repository's: a name that is a slug becomes the slug and the display name, and any other name becomes the display name
with a slug derived from it. Ingest tokens typed under such a name keep working when the name was already a slug.

## Ingesting work items

`blizzard hub chunk ingest` takes one or more source-native tokens and mints a chunk; each token is `<source>:<ref>`,
`<source>#<ref>`, or a pasted work-item URL. The CLI parses nothing. When a token's `<source>` is an active source's id,
slug or former slug, the hub hands the token to that source alone, and a token naming a retired source is refused `422`;
`hub` goes to the built-in source. Any other token, a URL included, is resolved against every active source's own parse.

The chunk is minted in one project. `--project <id|slug>` names it; without it, the hub uses the one project every
token's source is linked to, and answers `422 project_required`, listing the tenant's projects, when the sources are
linked to several projects or to different ones. Every token's source must be linked to that project, or the ingest
answers `422 source_not_linked`, naming the source: a new source serves no project until one links it
([projects.md](./projects.md#links)). The built-in `hub` source is linked to every project and takes no part in the
choice; an ingest of hub items alone goes to the project the items were created in, when they agree.

The active source list is also the hub's allowlist of ingestable repos: a token naming a repo no active source covers is
rejected 422, naming the token and the sources that are configured — adding a repo to the fleet means creating its
record first, with no separate allowlist to sync. For the github provider, `ref` must be numeric (the issue number); a
non-numeric ref matches no configured source's parse and surfaces as the same 422 an unconfigured repo gets, so a
malformed ref misdiagnoses as a missing source.

The legacy `github:<rest>` token prefix is deprecated: it still resolves — warning on stderr, then passing the rest on
its own merits — but carries no provider selection anymore, since a token resolves against whichever configured source
claims it.

## Hub-owned items

Creating a hub item mints its resting chunk in the same write, in the project the call names
(`blizzard hub item create
--project <id|slug>`, needed only when the tenant holds several projects): POST to the hub
source's items pins a fresh `not_ready` chunk to the default graph holding the new item's pointer and returns the chunk
id alongside the item, so nothing separate needs ingesting.

Item mutation (the four verbs under `/api/work-sources/{source}/items`) is served only by the built-in hub source, whose
own store is a hub-owned item's system of record; a request against a configured forge source refuses with a 409 naming
it on all four verbs, by design rather than a missing opt-in knob. Withdrawing a hub item (DELETE) refuses with a 409
only while its chunk is genuinely acquired and still live — stop the chunk first; an unacquired holder (never claimed,
`not_ready` or `ready`) is deleted along with the withdrawal instead of blocking it
([control-verbs.md](./control-verbs.md#delete) owns the pairing's own mechanics).

## Forge-status labels (`annotate`)

`annotate` (default false) opts a source into the forge-status label sweep: a periodic hub background sweep projects
every live chunk's status onto its forge issue as `blizzard:ingested` (minted but unclaimed — not_ready/ready) or
`blizzard:in-progress` (running, paused, waiting_on_human, needs_human, delivering); a chunk with no live holder or one
that reached stopped/done carries neither.

The label sweep runs every `annotation_interval_seconds` (a top-level `blizzard-hub.toml` key, default 120). It is the
hub's `forge_status` sweep, which can be stopped and run a pass at a time
([The hub's sweeps](./observability.md#the-hubs-sweeps)). It keeps no label state: a full pass discovers the forge's
actual labels afresh and writes only the difference from desired state. Each tick first checks, cheaply, whether
anything the hub projects has changed (a chunk's status or work refs, or which sources annotate) and skips the forge
reads when nothing has, running a full pass anyway once five intervals (ten minutes by default) have gone by. A change
on the hub side shows on the forge within one interval; a hand-removed label, an edit by another actor, or a source's
binding edit self-heals within that five-interval floor. Mid-sweep crashes, forge outages, and failed writes or clears
are retried on the very next pass. A forge that is down, slow, or rate-limiting degrades the label sweep to a skip of
that source, which records a `forge-status-source-skipped` warning in the tenant's event log (`GET /api/events`), naming
the source and its id — once per outage, not once per pass: a source read successfully again re-arms it, and a hub
restart during an outage announces it once more. A skip never blocks a chunk transition, an ingest, or any other hub
request. The sweep is among the first callers the forge's [rate budget](./forges.md#the-rate-budget) turns away: below
two fifths of the limit its source is skipped without a request, and the pass after the reset runs it again.

Each tenant is judged on its own. The change check and the five-interval floor are kept per tenant, so a tenant whose
source's forge cannot be read retries on every pass by itself while every other tenant keeps skipping unchanged passes,
and a fault raised in one tenant is logged against it without stopping the pass for the rest.

The one thing the sweep remembers, in the hub store, is which sources it annotates. A source whose `annotate` is turned
off across a restart has every blizzard label it carries cleared once on the next pass, through its still-configured
binding; a clear that fails is retried each pass until it finishes. A source whose record is retired is let go of the
same way: its labels are cleared on the next pass, through its still-readable record. A hub that never annotated a
source never clears it.

Set `annotate = true` on at most one hub per forge repo: two sweeps against one repo fight over the same labels with no
coordination — only the canonical instance opts in; every dev, staging, or snapshot hub pointed at the repo leaves it
false. A snapshot hub hosted on a copy of an annotating hub's store inherits that memory, so its first pass clears the
copied sources' labels once (the canonical hub's next pass sets them again) unless their records are retired.

## Delivered PRs

The PR a delivery opens carries a generated body: one line per work item of the chunk, the chunk id, and — when
`blizzard-hub.toml` declares `public_url`, the absolute `http(s)` origin the board is publicly reached at — a link to
the chunk's board page. The merge commit message is the PR title followed by the same work-item lines. A forge-sourced
item appears as the forge's own cross-link (`Refs owner/repo#12`), so the issue links back to the PR; an item on no
forge, the built-in `hub` source's included, appears as its label. With `public_url` unset the board link is omitted and
delivery is otherwise unchanged; never set it to the bind address, which a proxied hub is not reached at from outside.

No generated line ever puts a closing keyword (`closes`, `fixes`, `resolves`, or their variants) before a reference, and
no item title is copied in: the forge must never close an item on merge, because the close drain below is the only
closer and records each outcome. A PR opened before an upgrade keeps the body it was opened with.

## Delivery closure

Closure is unconditional per source — there is no per-source `close` flag to set. The transaction that lands a chunk (or
completes it by hand) enqueues one durable close intent per still-open work ref with a bound closer; a fixed drain sweep
(the hub's `close_drain` sweep, [The hub's sweeps](./observability.md#the-hubs-sweeps)), on its own short interval and
independent of `annotation_interval_seconds`, then attempts up to a fixed number of due intents per pass through that
source's binding, shared fairly among the tenants with due intents (one tenant's backlog never takes another's turn) and
leaving the rest to the next pass, and a failed attempt stays pending under backoff. A close the forge's
[rate budget](./forges.md#the-rate-budget) turns away, or the forge refuses for its rate, is not a failure: the intent
waits for the reset without spending its backoff, and is attempted on the first pass after it — the guarantee half of
closing delivered work, where a worker's own commit metadata is only an opportunistic hint that may beat the drain.
Unlike `annotate`, closing carries no *multi-writer* canonical constraint: a close is idempotent at the forge, so more
than one hub pointed at the same repo closing the same item is not a race to coordinate around.

It does carry an instance-level one: `close_forge_writes_enabled` (default `true`) gates whether *this hub* writes to
any configured source's forge at all. A non-canonical hub — dev, staging, or a restored snapshot — set it `false`; the
built-in `hub` source is unaffected (it writes no forge, so there is no live item to close in error), but every
configured source's closer is seated nowhere. A ref with no bound closer records a terminal `skipped` closure with
reason `no closer`, not a pending intent; a legacy pending intent found due without a closer is likewise skipped and
retired. Binding a closer later, including by turning this setting on, does not automatically resurrect skipped
closures. This is the knob `annotate`'s own canonical-instance discipline three paragraphs up has no equivalent for on
the closing side: closing still needs no per-repo single-writer coordination, but it does need a way for a hub that
should never touch a live forge to decline writing to one at all — creating a source record for label rendering (the
next section) would otherwise also silently grant close authority.

Before closing a GitHub issue the closer leaves one comment naming the merged PR and landed commit of each repo the
chunk landed, carrying a hidden per-chunk marker. The comment is posted only when no comment on the issue has that
marker, so a retried or redelivered close never posts a second one, and it lands before the close so an issue is never
closed without its trace. A chunk with no landed repo, completed by hand, gets no comment. There is no switch for the
comment: `close_forge_writes_enabled` gates it along with every other forge write the closer makes. Like a delivered PR,
it never places a closing keyword before a reference.

A stopped chunk that never landed closes nothing; a chunk that landed and was later stopped still closes — landing, not
chunk status, is what the drain gates on. Closing is best-effort and non-atomic: each ref is attempted independently,
one failure never blocks another, and a failed attempt retries on the next pass — no bound on how many passes a
transient forge outage costs, only eventual convergence while a closer remains bound. A source that cannot resolve a
closer is skipped as described above; a source whose secret cannot be revealed is an error, not an absent source.

Each ref's outcome (closed, gone, failed, or skipped) is recorded as a durable fact and announced by a chunk-linked
event: `work-item-closed` at info, `work-item-close-failed` at warning for a retried failure or terminal gone item, or
`work-item-close-skipped` at warning for no closer. Events are best-effort across a crash; the outcome is the durable
record.

## Upgrading a hub with existing external chunks

A hub's oldest external chunks may hold a work ref whose source is a repo tail — the part of the repo's name after the
last slash — written by an earlier release before work sources were records. The upgrade that keys sources by id
rewrites every ref naming a source its tenant holds to that source's id. A ref naming no source of its tenant is
**stranded**: the migration leaves it as stored, logs a warning per table with the count and up to five of them as
`tenant/value`, and goes on. Creating a source afterwards does not reattach it, because refs now name sources by id,
never by slug. So before pulling a release that crosses this upgrade, give every repo tail such a chunk names a source
record whose name is that tail. A hub still configured by `[[work_source]]` blocks is the same case: `import-legacy`
runs after `migrate` ([install.md](./install.md#work-sources-and-forge-settings-are-records)), so carry the blocks into
records with a release from before this upgrade, or its existing chunks' refs are stranded.

A stranded ref degrades without failing anything: its label renders as `{stored value}#{ref}`, and its entry in
`GET /api/chunks/<id>/work-items` carries `error="no work source is configured under '<stored value>'"`. Verify after
the upgrade by reading a pre-existing chunk's work items and confirming every entry's `error` is null.

## Upgrading past the `close` flag

Closure is unconditional, so a `close` key on a leftover `[[work_source]]` block has nothing left to opt into;
`import-legacy` drops it when it carries the block into a record.

The migration that ships beside this wheel backfills a close intent for every already-landed or hand-completed work ref
still carrying no terminal outcome, regardless of whether its source ever set `close = true` — because no deployment
ever did, the drain works through the accumulated backlog of delivered forge items over the passes after the upgrade, a
fixed number of attempts each, and a failed attempt waits under backoff. That is the intended repair, not a bug: expect
a burst of `work-item-closed` events, one per backlog ref, in the minutes after the restart.
