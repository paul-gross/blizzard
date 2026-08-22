# Configuring work sources

The hub's work-item pass-through reads every chunk's work item through a **configured work source** — a named,
credentialed binding to one forge repo, declared as an `[[work_source]]` table in `blizzard-hub.toml`. This is a
separate seam from the [delivery forge](./install.md): `BZ_FORGE_URL`/`BZ_FORGE_TOKEN` in the hub's env file control
where a chunk's PR is opened and landed; `[[work_source]]` controls where its work item is *read from*, and each source
carries its own credential rather than sharing the delivery forge's.

A bare hub with zero `[[work_source]]` blocks is a legal, fully-operable deployment: the built-in `hub` source is always
seated, needs no configuration and no credential, and is what a hub-authored work item (`hub:<n>`) resolves through.
`[[work_source]]` blocks are for **external** forge repos only — configure one per repo you want the hub to ingest work
items from.

`blizzard hub init` scaffolds a commented-out example block — uncomment it and fill in your own repo to configure a
source:

```toml
[[work_source]]
name = "blizzard"                                  # source id — ingest tokens and board labels key on it
provider = "github"                                # the only adapter grammar today
repo = "paul-gross/blizzard"                       # the "owner/repo" this source is pinned to
token_env = "BZ_WORK_SOURCE_TOKEN"                          # names an env var — see credentials below
annotate = false                                   # opt into the forge-status label sweep — see below
close = false                                      # opt into the delivery closure sweep — see below
# api_base = "https://ghe.example.internal/api/v3" # optional: override the provider's API origin
# web_base = "https://ghe.example.internal"         # optional: override the web origin
```

Every field:

| Field       | Required            | Meaning                                                                                                                                                                                                                                                                                                                                                  |
| ----------- | ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`      | yes                 | The source's identity. Ingest tokens (`name:ref`, `name#ref`) and board pointer labels (`{source}#{ref}`) key on it. Must not contain `:` (the ingest token grammar splits on the first one). Must be unique across all `[[work_source]]` blocks. `hub` is reserved for the built-in, always-seated hub work source (see below) — no block may claim it. |
| `provider`  | yes                 | The adapter grammar this source speaks. Only `"github"` exists today; an unknown provider fails at config load, not at first use.                                                                                                                                                                                                                        |
| `repo`      | yes                 | The `owner/name` coordinate this source is pinned to. Each `(provider, repo)` pair may appear under only one `name` — two names for the same repo would let one item be ingested twice under two identities.                                                                                                                                             |
| `token_env` | yes                 | Names an environment variable — **not the secret itself**. See "Credential indirection" below.                                                                                                                                                                                                                                                           |
| `annotate`  | no, default `false` | Opts this source into the forge-status label sweep. See "The forge-status label projection" below — **do not set this on more than one hub against the same forge repo.**                                                                                                                                                                                |
| `close`     | no, default `false` | Opts this source into the delivery closure sweep. See "Closing delivered work items" below — **do not set this on more than one hub against the same forge repo.**                                                                                                                                                                                       |
| `api_base`  | no                  | Overrides the provider's default API origin. Required to reach a self-hosted forge (e.g. GitHub Enterprise).                                                                                                                                                                                                                                             |
| `web_base`  | no                  | Overrides the provider's default web origin, used for the item's browsable URL. Derived from `api_base` when omitted, so a self-hosted GHE source only needs to set `api_base`.                                                                                                                                                                          |

Unlike `annotate`/`close`, item mutation (`GET`/`POST`/`PATCH`/`DELETE` under `/api/work-sources/{source}/items`) has no
`[[work_source]]` knob to opt a configured source into: it is served only by the built-in `hub` source, whose own store
is a hub-owned item's system of record. An operator request against a configured forge source refuses with a 409 naming
it, on all four verbs — by design, not by a missing flag a future block could set. Creating a hub item mints its resting
chunk in the same write: `POST .../hub/items` pins a fresh `not_ready` chunk to the default graph, holding the new
item's pointer, and returns the chunk's id alongside the item — the fleet already has something to promote the moment
the item exists, with no separate ingest call. Withdrawing that item (`DELETE`) refuses with a 409 while the chunk is
still live — the same guard that refuses a re-ingest of a pointer a live chunk already holds; stop the chunk first.

**A self-hosted GitHub Enterprise example** — an internal repo behind a company GHE instance, alongside the public
`blizzard` source:

```toml
[[work_source]]
name = "internal"
provider = "github"
repo = "acme/internal-tool"
token_env = "BZ_INTERNAL_TOKEN"
api_base = "https://git.corp.internal/api/v3"
web_base = "https://git.corp.internal"        # explicit override illustration only —
                                               # api_base alone is enough (web_base derives
                                               # from it); shown here so the override syntax
                                               # is visible somewhere in this doc.
```

`name = "internal"` is a free choice **only** because `acme/internal-tool` is a brand-new source with no chunks minted
against it yet. That freedom does not extend to a repo that already has chunks in this hub — see the repo-tail rule in
the upgrade note below, which this example is not an illustration of.

## Credential indirection

`token_env` names an environment variable; the secret itself goes in the hub's env file (`/etc/blizzard/hub.env` under
the [systemd layout](./install.md)), never in `blizzard-hub.toml` — the same separation the delivery forge's
`BZ_FORGE_TOKEN` already follows. An unset `token_env` fails at boot, naming the missing variable rather than silently
ingesting unauthenticated.

## The forge-status label projection

Per source with `annotate = true`, the hub runs a periodic background sweep that projects every live chunk's status onto
its forge issue as one of two labels:

- `blizzard:ingested` — the chunk is minted but not yet claimed (`not_ready`/`ready`).
- `blizzard:in-progress` — a runner or the hub is actively working it (`running`, `paused`, `waiting_on_human`,
  `needs_human`, `delivering`).

A chunk with no live holder, or one that has reached `stopped`/`done`, carries neither label. The sweep runs every
`annotation_interval_seconds` seconds — a top-level `blizzard-hub.toml` key, default `120`, consulted only when at least
one source opts in — and holds **no state of its own**: each pass discovers the forge's actual labels afresh (listing
issues per label, not reading back what a prior sweep wrote) and writes only the difference from desired state. That
statelessness is what makes the projection self-healing: a label removed by hand, a crash mid-sweep, or a forge outage
all resolve themselves on the next pass, with no hub-side record to repair. A forge that is down, slow, or rate-limiting
degrades to a logged skip — it never blocks a chunk transition, an ingest, or any other hub request.

**Set `annotate = true` on at most one hub per forge repo.** Two writers sweeping the same repo will fight over the same
labels — each pass "correcting" what the other just wrote — with no coordination between them. Only the canonical
instance for a given forge repo should opt in; every dev/staging/snapshot hub pointed at that same repo must leave it
`false`.

## Closing delivered work items

Per source with `close = true`, the hub runs a periodic background sweep that closes every landed, non-grouped chunk's
still-open work refs through that source's own binding — the guarantee half of closing delivered work (issue #216); a
worker's own commit metadata, when the source honors it, is only an opportunistic hint that may beat this sweep to it on
a fast-forward landing.

Closing is **best effort and non-atomic**: each ref is attempted independently, one ref's failure never blocks
another's, and a failed attempt is retried on the sweep's next pass — there is no bound on how many passes a transient
forge outage costs, only that it eventually converges. Each ref's outcome (`closed`, `gone`, or `failed`) is recorded as
a durable fact and, the first time that outcome is recorded, one chunk-visible event (`work-item-closed` at `info`, or
`work-item-close-failed` at `warning` — covering both a retried `failed` attempt and a terminal `gone` one). The same
`annotation_interval_seconds` paces this sweep too — there is no second interval knob to configure.

**Set `close = true` on at most one hub per forge repo**, for the same reason as `annotate` above: two writers issuing
the same closing `PATCH` race each other with no coordination between them. A `STOPPED` chunk that never landed closes
nothing; a chunk that landed and was *later* stopped still closes — landing, not chunk status, is what this sweep gates
on.

## The upgrade note

**An existing hub with chunks pointing at external forge issues must add a matching `[[work_source]]` block, or their
board pointer labels go null on the next deploy:** rendering `{source}#{ref}` needs a configured source by that name,
and there is none to render against until one exists. The built-in `hub` source (issue #357) covers only hub-authored
work items (`hub:<n>`) — it does not stand in for an external repo's binding, so `GET /chunks/{id}/work-items` never
503s outright regardless, but an external chunk's entry still degrades to a null label and an `error` until its source
is configured.

This is not optional for a hub that already ingests external work items; there is no backward-compatible default,
because the work source list also bounds which repos the hub is willing to ingest from (see below). Add the
`[[work_source]]` block to `blizzard-hub.toml` as part of the same maintenance window as the wheel upgrade, before
running `migrate`/restarting the daemon (see the [install/upgrade steps](./install.md)).

**For a repo that already has chunks in this hub, `name` is not a free choice — it must be the repo's own tail** (the
part after the last `/`; e.g. `blizzard` for `paul-gross/blizzard`). An earlier release's migration
(`20260716_1512_hub_pm_pointer_source_ref`, which predates the `[[work_source]]` key and ran under its old name)
backfilled every existing pointer's `source` to its repo tail, so a `name` that does not match strands those pointers:
nothing 503s (the hub sees a non-empty source list and boots clean), but every pre-existing chunk for that repo silently
degrades — `label` goes `null` and its `work-items` entry carries
`error="no configured work source named '<repo-tail>'"`, because the pointer's `source` and the configured `name` no
longer agree. A repo with no chunks minted against it yet has no such constraint — any `name` is safe (the GHE example
above is exactly that case, not an illustration of the repo-tail rule).

**Verify you got it right** after the upgrade: for any chunk that existed before this release, read its work items and
confirm no entry carries an `error`:

```text
curl -s http://<hub>/api/chunks/<chunk_id>/work-items | jq '.items[].error'
```

Every value printed should be `null`. A non-null `error` naming a work source means the configured `name` does not match
the backfilled repo tail for that chunk's pointer — fix the `name` (or add a second `[[work_source]]` under the correct
tail) and restart.

## Ingest tokens

`blizzard hub chunk ingest` takes one or more source-native tokens and mints a chunk. Each token is one of:

- `<source>:<ref>` — e.g. `blizzard:26`
- `<source>#<ref>` — e.g. `blizzard#26`
- a pasted work item URL (e.g. the GitHub issue's own URL)

For the `github` provider, `<ref>` must be numeric (the issue number) — a `<source>:<ref>` or `<source>#<ref>` token
with a non-numeric `ref` (e.g. `blizzard:v2`) matches no configured source's `parse` and surfaces as the same 422 an
unconfigured repo gets ("not claimed by any configured work source"), which misdiagnoses as a missing `[[work_source]]`
rather than a malformed ref.

The CLI carries no parsing of its own: it hands the token to the hub, which resolves it against every configured
source's own `parse`. The legacy `github:<rest>` prefix is deprecated — it still resolves (warns on stderr, then passes
`rest` on its own merits) but carries no provider selection of its own anymore, since a token now resolves against
whichever configured source claims it.

## Unconfigured repos are a 422 at the front door

The configured source list is also the hub's allowlist of ingestable repos: a token that names a repo (via URL or an
unresolvable source name) that no `[[work_source]]` covers gets rejected with `422 Unprocessable Entity`, naming the
token and the sources that *are* configured. Adding a repo to the fleet means adding its `[[work_source]]` block first —
there is no separate allowlist to keep in sync.
