# Work sources

The hub reads every chunk's work item through a configured work source: a named, credentialed binding to one forge repo,
declared as an `[[work_source]]` table in `blizzard-hub.toml`. Work sources are a separate seam from the delivery forge:
`BZ_FORGE_URL`/`BZ_FORGE_TOKEN` control where a chunk's PR is opened and landed, `[[work_source]]` controls where its
work item is read from, and each source carries its own credential rather than sharing the delivery forge's.

A hub with zero `[[work_source]]` blocks is fully operable: the built-in hub source is always seated, needs no
configuration or credential, and resolves hub-authored work items (`hub:<n>`); `[[work_source]]` blocks are for external
forge repos only, one per repo to ingest from.

## Declaring a source

`blizzard hub init` scaffolds a commented-out example `[[work_source]]` block with every field annotated — uncomment and
fill it in to configure a source; the scaffold is the field-by-field reference.

- `name` is the source's identity: ingest tokens (`name:ref`, `name#ref`) and board pointer labels (`{source}#{ref}`)
  key on it; it must not contain a colon (the token grammar splits on the first one), must be unique across blocks, and
  `hub` is reserved for the built-in source. For a repo that already has chunks in this hub, `name` is not a free choice
  — it must be the repo's own tail, the part after the last slash: an earlier release's migration backfilled every
  existing pointer's source to its repo tail, so a mismatched name strands those pointers — nothing 503s and the hub
  boots clean, but every pre-existing chunk for that repo degrades silently, label null and its work-items entry
  carrying `error="no configured work source named '<repo-tail>'"`. A repo with no chunks minted against it yet carries
  no repo-tail constraint; any name is safe.
- `repo` is the owner/name coordinate the source is pinned to; each (provider, repo) pair may appear under only one
  name, since two names for one repo would let an item be ingested twice under two identities.
- `provider` names the adapter grammar; only `github` exists, and an unknown provider fails at config load, not first
  use.
- `api_base` overrides the provider's API origin (needed for a self-hosted forge such as GitHub Enterprise); `web_base`
  overrides the web origin for the item's browsable URL and derives from `api_base` when omitted, so a GHE source needs
  only `api_base`.
- `token_env` names an environment variable, never the secret itself; the secret goes in the hub's env file
  (`/etc/blizzard/hub.env` under the systemd layout in [install.md](./install.md)), and an unset `token_env` fails at
  boot naming the missing variable rather than silently ingesting unauthenticated.

## Ingesting work items

`blizzard hub chunk ingest` takes one or more source-native tokens and mints a chunk; each token is `<source>:<ref>`,
`<source>#<ref>`, or a pasted work-item URL. The CLI parses nothing; the hub resolves each token against every
configured source's own parse.

The configured source list is also the hub's allowlist of ingestable repos: a token naming a repo no `[[work_source]]`
covers is rejected 422, naming the token and the sources that are configured — adding a repo to the fleet means adding
its block first, with no separate allowlist to sync. For the github provider, `ref` must be numeric (the issue number);
a non-numeric ref matches no configured source's parse and surfaces as the same 422 an unconfigured repo gets, so a
malformed ref misdiagnoses as a missing `[[work_source]]`.

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

A worker's node-step may also propose work items as it completes — new items to file, or evidence to append to one
already open — and the hub materializes every accumulated, unstruck proposal of a chunk once that chunk actually
delivers, minting a fleet-authored hub item (or appending the evidence) best-effort and eventually convergent, never
blocking the delivery itself. An unresolvable proposal — its target closed, withdrawn, gone, or sourced somewhere with
no editor — is recorded with its reason rather than retried forever. An operator resolving a runner-config gate may
strike some of a chunk's pending proposals so they never materialize at all; striking, and materialization generally, is
owned by blizzard-context's
[`domain/work/chunk.md`](https://github.com/paul-gross/blizzard-context/blob/master/domain/work/chunk.md#materialization).

## Forge-status labels (`annotate`)

`annotate` (default false) opts a source into the forge-status label sweep: a periodic hub background sweep projects
every live chunk's status onto its forge issue as `blizzard:ingested` (minted but unclaimed — not_ready/ready) or
`blizzard:in-progress` (running, paused, waiting_on_human, needs_human, delivering); a chunk with no live holder or one
that reached stopped/done carries neither.

The label sweep runs every `annotation_interval_seconds` (a top-level `blizzard-hub.toml` key, default 120, consulted
only when a source opts in) and holds no state: each pass discovers the forge's actual labels afresh and writes only the
difference from desired state, so hand-removed labels, mid-sweep crashes, and forge outages all self-heal on the next
pass. A forge that is down, slow, or rate-limiting degrades the label sweep to a logged skip; it never blocks a chunk
transition, an ingest, or any other hub request.

Set `annotate = true` on at most one hub per forge repo: two sweeps against one repo fight over the same labels with no
coordination — only the canonical instance opts in; every dev, staging, or snapshot hub pointed at the repo leaves it
false.

## Delivery closure (`close`)

`close` (default false) opts a source into the delivery closure sweep: the hub periodically closes every landed,
non-grouped chunk's still-open work refs through that source's binding — the guarantee half of closing delivered work,
where a worker's own commit metadata is only an opportunistic hint that may beat the sweep on a fast-forward landing.
The same `annotation_interval_seconds` paces the closure sweep; there is no second interval knob. Set `close = true` on
at most one hub per forge repo, for the same uncoordinated-writers race as `annotate`.

A stopped chunk that never landed closes nothing; a chunk that landed and was later stopped still closes — landing, not
chunk status, is what the closure sweep gates on. Closing is best-effort and non-atomic: each ref is attempted
independently, one failure never blocks another, and a failed attempt retries on the next pass — no bound on how many
passes a transient forge outage costs, only eventual convergence.

Each ref's outcome (closed, gone, or failed) is recorded as a durable fact and, the first time recorded, one
chunk-visible event: work-item-closed at info, or work-item-close-failed at warning, the latter covering both a retried
failed attempt and a terminal gone one.

## Upgrading a hub with existing external chunks

An existing hub with chunks pointing at external forge issues must add a matching `[[work_source]]` block or their board
pointer labels render null on the next deploy: `{source}#{ref}` needs a configured source by that name, and the built-in
hub source covers only hub-authored items — `GET /chunks/{id}/work-items` never 503s, but an external chunk's entry
degrades to a null label and an error until its source is configured. There is no backward-compatible default, because
the source list also bounds which repos the hub will ingest from; add the block in the same maintenance window as the
wheel, before migrate and restart ([install.md](./install.md) owns the sequence).

Verify after the upgrade by reading a pre-existing chunk's work items (`GET /api/chunks/<id>/work-items`) and confirming
every entry's `error` is null; a non-null error naming a source means the name does not match the backfilled repo tail —
fix it, or add a second block under the correct tail, and restart.
