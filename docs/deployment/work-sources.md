# Work sources

The hub reads every chunk's work item through a configured work source: a named, credentialed binding to one forge repo,
stored as a record and managed with `blizzard hub source`. Work sources are independent of delivery's forge access: a
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
blizzard hub source show blizzard
```

- `name` is the source's identity: ingest tokens (`name:ref`, `name#ref`) and board pointer labels (`{source}#{ref}`)
  key on it; it must not contain a colon (the token grammar splits on the first one), and `hub` is reserved for the
  built-in source. For a repo that already has chunks in this hub, `name` is not a free choice — it must be the repo's
  own tail, the part after the last slash: an earlier release's migration backfilled every existing pointer's source to
  its repo tail, so a mismatched name strands those pointers — nothing 503s and the hub boots clean, but every
  pre-existing chunk for that repo degrades silently, label null and its work-items entry carrying
  `error="no configured work source named '<repo-tail>'"`. A repo with no chunks minted against it yet carries no
  repo-tail constraint; any name is safe.
- `locator` is the owner/name coordinate the source is pinned to; each (provider, locator) pair belongs to one record,
  since two names for one repo would let an item be ingested twice under two identities.
- `provider` names the adapter grammar; only `github` exists.
- `api_base` overrides the provider's API origin (needed for a self-hosted forge such as GitHub Enterprise); `web_base`
  overrides the web origin for the item's browsable URL and derives from `api_base` when omitted, so a GHE source needs
  only `api_base`.
- `secret` names the stored secret holding the forge token, revealed only when the hub calls the forge.

`create` stores the source at revision 1. Every later change moves the revision by one:

- `edit <name>` changes only the flags given: `--provider`, `--locator`, `--secret`, `--api-base`, `--web-base`, and
  `--annotate` or `--no-annotate`. `--clear api-base`, `--clear web-base`, or `--clear secret` unsets a field, and
  `--clear secret` is refused for a provider that needs a credential, which includes `github`. An edit that leaves every
  field as it is writes nothing and keeps the revision. The name never changes.
- `retire <name>` hides the source from `list` unless `--include-retired` is given, and ingest naming it is refused with
  a 422. Items already ingested from it keep their labels, close and annotate through it. A retired source keeps its
  provider and locator, so `create` for another name on the same locator is refused with a message naming the holder;
  `enable` the holder instead.
- `enable <name>` lifts the retirement, and is refused while the source's secret is retired.

`edit`, `retire`, and `enable` take `--if-match <revision>` and are refused, naming the current revision, when the
source has moved on. A name may not contain `:` and may not be `hub`, which is the built-in source: `list` shows it as
built-in, and every attempt to change it is refused. Every write is recorded in the [change log](./config-changes.md).

Writes need the `config:edit` permission and reads need `fleet:view`. The record fields are also served over
`GET /api/work-sources` and `GET /api/work-sources/{name}`, and the document schema at
`GET /api/config/schema/work-sources`. A source whose secret cannot be revealed is an error on every call through it,
never an absent source.

The board's Admin page lists work sources, with each record's revision, last change, and history. On a desktop, a user
with `config:edit` can also create, edit, retire, and enable them there; an edit shows the fields that will change
before it saves. On a phone the page is read-only and names the `blizzard hub source` command that makes the change. The
built-in `hub` source is listed and cannot be changed.

## Ingesting work items

`blizzard hub chunk ingest` takes one or more source-native tokens and mints a chunk; each token is `<source>:<ref>`,
`<source>#<ref>`, or a pasted work-item URL. The CLI parses nothing; the hub resolves each token against every
configured source's own parse.

The active source list is also the hub's allowlist of ingestable repos: a token naming a repo no active source covers is
rejected 422, naming the token and the sources that are configured — adding a repo to the fleet means creating its
record first, with no separate allowlist to sync. For the github provider, `ref` must be numeric (the issue number); a
non-numeric ref matches no configured source's parse and surfaces as the same 422 an unconfigured repo gets, so a
malformed ref misdiagnoses as a missing source.

The legacy `github:<rest>` token prefix is deprecated: it still resolves — warning on stderr, then passing the rest on
its own merits — but carries no provider selection anymore, since a token resolves against whichever configured source
claims it.

## Hub-owned items

Creating a hub item mints its resting chunk in the same write: POST to the hub source's items pins a fresh `not_ready`
chunk to the default graph holding the new item's pointer and returns the chunk id alongside the item, so nothing
separate needs ingesting.

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

The label sweep runs every `annotation_interval_seconds` (a top-level `blizzard-hub.toml` key, default 120). It keeps no
label state: a full pass discovers the forge's actual labels afresh and writes only the difference from desired state.
Each tick first checks, cheaply, whether anything the hub projects has changed (a chunk's status or work refs, or which
sources annotate) and skips the forge reads when nothing has, running a full pass anyway once five intervals (ten minutes
by default) have gone by. A change on the hub side shows on the forge within one interval; a hand-removed label, an edit
by another actor, or a source's binding edit self-heals within that five-interval floor. Mid-sweep crashes, forge
outages, and failed writes or clears are retried on the very next pass. A forge that is down, slow, or
rate-limiting degrades the label sweep to a logged skip; it never blocks a chunk transition, an ingest, or any other hub
request.

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
completes it by hand) enqueues one durable close intent per still-open work ref, through whichever source owns that ref;
a fixed drain sweep, on its own short interval and independent of `annotation_interval_seconds`, then attempts up to a
fixed number of due intents per pass through that source's binding, leaving the rest to the next pass, and a failed
attempt stays pending under backoff — the guarantee half of closing delivered work, where a worker's own commit metadata
is only an opportunistic hint that may beat the drain. Unlike `annotate`, closing carries no *multi-writer* canonical
constraint: a close is idempotent at the forge, so more than one hub pointed at the same repo closing the same item is
not a race to coordinate around.

It does carry an instance-level one: `close_forge_writes_enabled` (default `true`) gates whether *this hub* writes to
any configured source's forge at all. A non-canonical hub — dev, staging, or a restored snapshot — set it `false`; the
built-in `hub` source is unaffected (it writes no forge, so there is no live item to close in error), but every
configured source's closer is seated nowhere, and its pending intents stay pending, logged, exactly like an intent for a
source removed from config — never dropped, never retried against the forge. This is the knob `annotate`'s own
canonical-instance discipline three paragraphs up has no equivalent for on the closing side: closing still needs no
per-repo single-writer coordination, but it does need a way for a hub that should never touch a live forge to decline
writing to one at all — creating a source record for label rendering (the next section) would otherwise also silently
grant close authority.

Before closing a GitHub issue the closer leaves one comment naming the merged PR and landed commit of each repo the
chunk landed, carrying a hidden per-chunk marker. The comment is posted only when no comment on the issue has that
marker, so a retried or redelivered close never posts a second one, and it lands before the close so an issue is never
closed without its trace. A chunk with no landed repo, completed by hand, gets no comment. There is no switch for the
comment: `close_forge_writes_enabled` gates it along with every other forge write the closer makes. Like a delivered PR,
it never places a closing keyword before a reference.

A stopped chunk that never landed closes nothing; a chunk that landed and was later stopped still closes — landing, not
chunk status, is what the drain gates on. Closing is best-effort and non-atomic: each ref is attempted independently,
one failure never blocks another, and a failed attempt retries on the next pass — no bound on how many passes a
transient forge outage costs, only eventual convergence. An intent whose source record no longer resolves stays pending
rather than failing or being dropped; a pending intent with no matching source is the operator-visible sign of a source
removed too early.

Each ref's outcome (closed, gone, or failed) is recorded as a durable fact and, the first time recorded, one
chunk-visible event: work-item-closed at info, or work-item-close-failed at warning, the latter covering both a retried
failed attempt and a terminal gone one.

## Upgrading a hub with existing external chunks

An existing hub with chunks pointing at external forge issues must hold a matching source record or their board pointer
labels render null on the next deploy: `{source}#{ref}` needs a configured source by that name, and the built-in hub
source covers only hub-authored items — `GET /chunks/{id}/work-items` never 503s, but an external chunk's entry degrades
to a null label and an error until its source is configured. There is no backward-compatible default, because the source
list also bounds which repos the hub will ingest from; create the record in the same maintenance window as the wheel,
before migrate and restart ([install.md](./install.md) owns the sequence).

Verify after the upgrade by reading a pre-existing chunk's work items (`GET /api/chunks/<id>/work-items`) and confirming
every entry's `error` is null; a non-null error naming a source means the name does not match the backfilled repo tail —
fix it, or add a second record under the correct tail, and restart.

## Upgrading past the `close` flag

Closure is unconditional, so a `close` key on a leftover `[[work_source]]` block has nothing left to opt into;
`import-legacy` drops it when it carries the block into a record.

The migration that ships beside this wheel backfills a close intent for every already-landed or hand-completed work ref
still carrying no terminal outcome, regardless of whether its source ever set `close = true` — because no deployment
ever did, the drain works through the accumulated backlog of delivered forge items over the passes after the upgrade, a
fixed number of attempts each, and a failed attempt waits under backoff. That is the intended repair, not a bug: expect
a burst of `work-item-closed` events, one per backlog ref, in the minutes after the restart.
