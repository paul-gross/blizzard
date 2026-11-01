# Forges

A forge is the server that hosts a tenant's repositories and work items, such as github.com or a GitHub Enterprise host.
A hub holds each one as a stored record, managed with `blizzard hub forge`. A forge holds no credential: every
[repository](./repositories.md) and [work source](./work-sources.md) names the forge it lives on and carries a stored
[secret](./secrets.md) of its own.

```bash
blizzard hub forge create github --kind github --api-base https://api.github.com
blizzard hub forge list
blizzard hub forge get github
```

## Fields

| Field      | Meaning                                                                                                           |
| ---------- | ----------------------------------------------------------------------------------------------------------------- |
| `forge_id` | `frg_` followed by a ULID. It never changes, and every repository and work source stores it                       |
| `slug`     | The handle you type, named like a [repository's](./repositories.md#naming-a-repository). It may be edited         |
| `name`     | The display name, the slug when `create` is not given one                                                         |
| `kind`     | What the server is. Only `github` exists. It is set at creation and never changes                                 |
| `api_base` | The server's API origin, such as `https://api.github.com`                                                         |
| `web_base` | The server's web origin, or null. When null the web origin derives from `api_base`; `web_origin` shows the result |

The kind decides which adapter speaks to the server and which operations a landing may rely on
([A missing capability](#a-missing-capability)).

Both addresses are stored as an origin in one canonical form: scheme and host lowercased, a default port and trailing
slashes dropped, the path kept as typed. An address with no `http` or `https` scheme, no host, whitespace, a user or
password, a query, or a fragment is refused `422`. Two spellings of one origin are one origin, so a tenant holds one
forge per server: a create or edit whose `api_base` another forge holds is refused `409 forge_origin_taken`, naming that
forge.

## Verbs

`create <slug>` stores the forge at revision 1. Every later change moves the revision by one:

- `edit <forge>` changes only the flags given: `--slug`, `--name`, `--api-base`, and `--web-base`. `--clear-web-base`
  returns the web origin to the derived one. A forge's kind cannot change: `edit` has no flag for it, a `PATCH` body has
  no field for it, and a configuration document entry whose `kind` differs from the stored forge's is refused
  `forge_kind_immutable`. An edit that leaves every field as it is writes nothing and keeps the revision. An edit of an
  address reaches every repository and work source that names the forge on its next use, with no restart.
- `retire <forge>` hides the forge from `list` unless `--include-retired` is given. A forge that an enabled repository
  or enabled work source names is refused `409 forge_in_use`, listing those records. A retired forge keeps its slug and
  API origin, so no other forge takes either.
- `enable <forge>` lifts the retirement. Enabling a repository or work source whose forge is retired is refused
  `422 forge_retired`, naming the forge, so enable the forge first.

`get`, `edit`, `retire` and `enable` take the forge's id or its slug. `get` lists the repositories and work sources that
name the forge. A renamed forge remains addressable by its former slug until another forge takes it, but a
[configuration document](./config-documents.md) must use the current slug. `edit`, `retire`, and `enable` take
`--if-match <revision>` and are refused, naming the current revision, when the forge has moved on. Every write is
recorded in the [change log](./config-changes.md).

Writes need the `config:edit` permission and reads need `fleet:view`. Over HTTP the verbs are `GET` and `POST`
`/api/forges`, `GET` and `PATCH` `/api/forges/{forge}`, and `POST /api/forges/{forge}/retire` and `/enable`, where
`{forge}` is an id or a slug. A create body carries `slug`, `kind` and `api_base`, with optional `name` and `web_base`;
a `PATCH` applies only the fields present. The forge's document schema is served at `GET /api/config/schema/forges`.

## Doors

A forge is configured through the same doors as the records that name it:

- The CLI, as above.
- A version-2 [configuration document](./config-documents.md)'s `forges` section, reconciled before the work sources and
  repositories that name its entries, so one file declares a forge and what lives on it. An apply never retires a forge
  and refuses an entry whose `kind` differs from the stored forge's.
- The board's Admin page, whose Forges tab lists each forge with its revision, history, and the records that name it,
  and lets a user with `config:edit` create, edit, retire and enable one on a desktop. On a phone the tab is read-only
  and names the `blizzard hub forge` command that makes the change. A forge's detail shows its capabilities and each
  budget's last reading, or "no reading yet" before the forge has answered.

## Records that name a forge

A repository and a work source each name their forge by `forge`, an id or a slug, and are stored with its id. Reads
carry `forge_id`, `forge_slug`, and `forge_name`. A work source's forge must be of its provider's kind: another kind is
refused `422 forge_kind_mismatch`. An unknown or retired forge is refused `422 forge_unavailable`. A repository create
naming neither `forge` nor `forge_api_url` is refused `422 forge_required`.

The board links a commit's branch through its project's linked repository record and that record's forge, regardless of
the chunk's work-item source. A missing or ambiguous repository match gives no link. The GitHub binding derives the
branch address from the forge's web origin, so a `web_base` edit moves the links of repositories that name it on the
next read; a forge kind without a browser-link binding gives no branch link.

### The old address fields

Before forges, each record carried the server's address itself: `forge_api_url` on a repository, `api_base` and
`web_base` on a work source. These fields are deprecated and still accepted, with a warning, until a later release
retires them:

- A write that sends only an old field resolves it to the tenant's forge with that API origin, and creates the forge
  when none exists. The new forge's slug and name come from the address, with `-2`, `-3` and so on added when the tenant
  already holds the slug.
- A write that sends an old field beside `forge` is accepted when the field agrees with the forge, and refused
  `422 forge_conflict` when it does not. A work source's `web_base` agrees only with its forge's web origin, so set the
  web origin on the forge instead.
- An old field whose origin a retired forge holds is refused `422 forge_unavailable`; enable the forge instead.
- A work source created with neither `forge` nor `api_base` names the tenant's `https://api.github.com` forge. An edit
  that omits both leaves the forge as it is, while `--clear api-base` re-points the source at that forge.
- Every read still answers the old fields, derived from the forge and marked deprecated in the OpenAPI document.

A write that leaned on an old field answers with a `Deprecation` header and a `warnings` entry for each, naming the
field and the forge it resolved to, and the hub records one `config-old-field-used` event per field
([observability.md](./observability.md#the-operational-event-log)). Once no event appears, nothing still sends an old
field. A version-1 configuration document that carries an address is such a write.

The `repositories.forge_api_url`, `work_sources.api_base` and `work_sources.web_base` store columns are deprecated too.
The hub keeps them equal to the forge on every write and every forge edit, and only a hub rolled back past the migration
reads them.

[Checking forge addresses before the forge migration](./repositories.md#checking-forge-addresses-before-the-forge-migration)
owns the pre-flight check an upgrade runs first.

## The rate budget

The hub keeps one rate budget per forge and secret — a source or repository with no secret shares the forge's anonymous
budget — in memory, shared by every caller that reaches the forge under that key: a `deliver` step's landing, the commit
check, and each work source's forge-status labels, closes and fetches. The budget learns from the `X-RateLimit-Limit`,
`X-RateLimit-Remaining` and `X-RateLimit-Reset` headers the forge answers with, and once its remaining falls it turns
the least important callers away before a request is sent, so each one waits for the reset rather than spending the last
of the budget and failing:

| Caller                                                                  | Sent while the remaining is   |
| ----------------------------------------------------------------------- | ----------------------------- |
| A landing (`land` and the step's other forge reads and writes)          | above none                    |
| The work-source sweeps — labels, closes, fetches — and the commit check | above two fifths of the limit |

A budget the forge has not answered yet, one answered without rate headers, one whose reset has passed, and every budget
after a hub restart send every caller. A forge that refuses a call for its rate — a `403` or `429` with nothing left, a
`Retry-After`, or a secondary limit — holds every caller of that key until the refusal lifts, a secondary limit for at
least a minute. A landing turned away answers `pending` and is polled again at the reset.

A work source's batch read goes out as one GraphQL request, which draws from the forge's `graphql` bucket rather than
its `core` one. It is admitted through the same gate, and the budget learns the bucket from the answer's
`X-RateLimit-Resource`. A GraphQL answer of `200` whose errors say `RATE_LIMITED` is a refusal like a `403` with nothing
left: it holds every caller of that key until the answer's `X-RateLimit-Reset`.

`blizzard hub forge get <forge>` prints each budget's last reading as a `budget` line — the secret, or `(anonymous)`,
the bucket, the remaining over the limit, and the reset — and `GET /api/forges/<forge>` answers the same as `budgets`.
Nothing is listed before the forge has answered.

The hub records `forge-budget-low` (warning) when a budget first turns a caller away, and `forge-rate-limited` (warning)
when the forge first refuses one for its rate — each at most once per budget per reset window, naming the forge, the
secret, the bucket, the last remaining and limit, and the reset
([observability.md](./observability.md#the-operational-event-log)). A notice lost to a restart is not recorded.

## A missing capability

Each action the hub drives against a forge declares the operations it cannot do without, and each forge kind offers a
set of them. `land`, for one, reads refs, compares revisions, opens, updates and merges changes, and reads checks.
Before a `deliver` step's pass, the hub checks every resolved repository's forge against the action's list. A repository
whose forge lacks one refuses the visit: the hub records a `forge-capability-missing` event naming the capability, the
repository, and the forge, writes a `hub-log.forge-capability-missing` artifact, and escalates the chunk to a person
with cause `forge-capability-missing`, before the action runs. Only an operator can change what a forge offers, so the
remedy is a forge of a kind that offers the capability, or an action that does not ask for it.

An action can live without some operations. `land` re-runs a failing check once only when the forge offers it, and
otherwise routes the failure as it would after one re-run.
