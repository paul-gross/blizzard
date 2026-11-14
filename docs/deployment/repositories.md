# Repositories

A hub holds the repositories work lands in as stored records, managed with `blizzard hub repo`. Each record names one
repository on a [forge](./forges.md) and the [credential](./credentials.md) that authenticates against it, and has a
slug you type, a display name, and an id the hub mints:

```bash
printf '%s' "$TOKEN" | blizzard hub secret create gh-token
blizzard hub forge create github --kind github --api-base https://api.github.com
blizzard hub credential create gh-token --forge github --binding github.token --secret gh-token --variant classic
blizzard hub repo create blizzard --forge github --owner paul-gross --repo blizzard \
  --base-branch master --credential gh-token
blizzard hub repo list
blizzard hub repo get blizzard
```

[forges.md](./forges.md) owns the forge record: its kind, its API and web origins, and the doors it is managed through.
A repository stores the forge's id, and a forge in use cannot be retired.

## Naming a repository

A repository carries three names, each with one job:

| Name            | What it is                                                                                                                                                                                                                               |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `repository_id` | `repo_` followed by a ULID. It never changes, and everything that refers to the repository stores it                                                                                                                                     |
| `slug`          | The handle you type: in CLI arguments, API paths, project links and configuration documents. 1 to 64 lowercase letters, digits and hyphens, a hyphen only between two letters or digits. Unique in the tenant; two tenants may share one |
| `name`          | The display name: 1 to 128 characters with no leading or trailing whitespace, unique nowhere. Only ever shown, never looked up. It is the slug when `create` is not given one                                                            |

Wherever a repository is accepted, its id or its slug names it; a display name never does, and neither does a commit's
`repo`: a commit pointer matches a record by forge coordinate, below. An id names its repository whatever the slug is
now.

A repository's slug and name may change, and nothing that refers to the repository moves: its project links, its
landings and its change-log rows keep pointing at it. The slug a repository leaves becomes its **former slug**, and a
URL, a script or a project link written against it keeps reaching the repository, until another repository takes that
slug. From then on it names its new holder. The one place a former slug is refused is a configuration document, which
answers `422` naming the repository that left it ([config-documents.md](./config-documents.md)). A configuration that
must survive any slug change names repositories by id.

A hub upgraded from a release whose repositories were named by `name` carries each name forward. A name that is a slug
becomes the slug and the display name. Any other name, such as `Odd Name`, becomes the display name, and the slug is
that name lowercased with each run of other characters collapsed to one hyphen (`odd-name`), suffixed `-2`, `-3` and so
on where the tenant already holds it. The old name stops resolving: it is not a slug, so it cannot be a former slug.
Name such a repository by its new slug.

## Fields

| Field                | Meaning                                                                                                                                                |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `slug`               | The handle you type. It may be edited; the one it leaves still resolves.                                                                               |
| `name`               | The display name, free text. It may be edited.                                                                                                         |
| `forge`              | The forge it lives on, by id or slug; reads carry `forge_id`, `forge_slug` and `forge_name`.                                                           |
| `owner`              | The repository's owner on the forge.                                                                                                                   |
| `repo`               | The repository's name on the forge.                                                                                                                    |
| `base_branch`        | The branch work lands on — the default a project's link may override ([projects.md](./projects.md#a-links-base-branch)).                               |
| `credential`         | The [credential](./credentials.md) it acts with, by id or slug (`--credential`); reads carry `credential_id`, `credential_slug` and `credential_name`. |
| `secret_name`        | The stored [secret](./secrets.md) the credential reads. `--secret` still names the `github.token` credential reading it.                               |
| `landing_policy`     | How the repository's changes land (`--policy`): `merge-on-checks`, the default.                                                                        |
| `allow_no_ci`        | Opt in to landing without CI (`--allow-no-ci`); defaults to false.                                                                                     |
| `park_timeout_hours` | Optional (`--park-timeout`). Only a repository whose policy parks changes takes one, so `merge-on-checks` refuses it.                                  |

Every field but `landing_policy`, `allow_no_ci` and `park_timeout_hours` is required; a create without `forge` is
refused `422 forge_required`. `landing_policy` defaults to `merge-on-checks` and cannot be cleared; `park_timeout_hours`
is the one field that can, with `edit --clear park-timeout` or a `PATCH` sending `null`.
[Delivery policy](./delivery-policy.md) helps choose and operate each policy. A policy is refused
`422 forge_capability_missing` when the repository's forge does not offer every capability it needs, naming each one;
`GET /api/config/schema/repositories` lists each policy with the capabilities it requires, and `blizzard hub forge get`
lists the capabilities a forge offers. Each `(forge, owner, repo)` belongs to one record. The forge carries the API
origin, so a `forge_api_url` is refused `422 field_moved_to_forge`, naming the forge
([forges.md](./forges.md#records-that-name-a-forge)).

### The forge token

[credentials.md](./credentials.md#setting-up-a-github-token) owns the token a repository acts with: the permissions
delivery needs on GitHub, and what a fine-grained token cannot do.

## Verbs

`create <slug>` stores the repository at revision 1, named `--name` (default: the slug), landing by `--policy` (default:
`merge-on-checks`). It is refused when another repository holds the slug as its current one, when the forge, owner and
repo are already taken, when the credential or secret is missing or retired, or when the forge cannot honour the policy.
Taking a slug another repository left moves it to the new repository. Every later change moves the revision by one:

- `edit <repository>` changes only the flags given: `--slug`, `--name`, `--forge`, `--owner`, `--repo`, `--base-branch`,
  `--credential`, `--secret`, `--policy`, `--allow-no-ci` / `--no-allow-no-ci`, and `--park-timeout`;
  `--clear park-timeout` clears the timeout, and is refused beside `--park-timeout`. An edit that leaves every field as
  it is writes nothing and keeps the revision.
- `retire <repository>` hides the repository from `list` unless `--include-retired` is given. A retired repository keeps
  its forge, owner and repo, so `create` for another slug on the same coordinate is refused with a message naming the
  holder; `enable` the holder instead.
- `enable <repository>` lifts the retirement, and is refused while the repository's secret is retired.

`get`, `edit`, `retire` and `enable` take the repository's id or its slug.

`edit`, `retire`, and `enable` take `--if-match <revision>` and are refused, naming the current revision, when the
repository has moved on. An active repository keeps its secret from being retired. Every write is recorded in the
[change log](./config-changes.md).

Writes need the `config:edit` permission and reads need `fleet:view`. Over HTTP the verbs are `GET` and `POST`
`/api/repositories`, `GET` and `PATCH` `/api/repositories/{repository}`, and
`POST /api/repositories/{repository}/retire` and `/enable`, where `{repository}` is an id or a slug. A body carries
`slug` and `name`; `name` defaults to `slug` on create. `PATCH` applies only the fields present and refuses an explicit
`null` on every field but `park_timeout_hours`, where it clears the timeout. The document schema is served at
`GET /api/config/schema/repositories`.

The board's Admin page lists repositories, with each record's revision, last change, and history. On a desktop, a user
with `config:edit` can create, edit, retire, and enable them there, seeing the fields that will change before an edit
saves. On a phone the page is read-only and names the `blizzard hub repo` command that makes the change. The repository
form picks an enabled forge by name instead of editing an address, and picks the landing policy: a policy the chosen
forge cannot honour is disabled and names the capabilities it lacks, and the park timeout appears only for `park`. A
repository's detail shows its landing policy, and for `park` its timeout, or `default` when none is set. A
[configuration document](./config-documents.md) names the forge by its slug in `forge`.

## Checking forge addresses before the forge migration

A later release gives each forge address one canonical form, and its migration refuses a store whose records it cannot
carry over without one of them changing. A hub that migrates as it starts turns that refusal into a failed deploy, so
settle the records first, on the hub you run today.

`blizzard hub config check-forges` reads the store's address columns directly, changes nothing, and names every record
the migration would refuse, by slug, with the reason. It judges every repository's `forge_api_url`, every GitHub work
source's `api_base` (an empty one reads as `https://api.github.com`) and every explicit `web_base`, retired records
included. It names four cases:

- `not-an-origin` — an address with no `http` or `https` scheme, no host, or a malformed host or port, such as
  `ghe.acme`. Edit the record's address to the server's origin.
- `needs-cleaning` — an address carrying a user, password, query, or fragment. Edit the record's address to the bare
  origin.
- `coordinate-collision` — repositories whose addresses differ only in spelling, such as a trailing slash, and so would
  hold one `owner/repo` coordinate. Retiring one does not settle it; edit one so the two no longer name one coordinate.
- `web-base-disagreement` — GitHub work sources declaring different `web_base` values for one API origin. Make the
  explicit ones agree.

Run it from the build you are about to install, against the runtime directory of the hub that still serves the store,
while that hub runs:

```bash
blizzard hub config check-forges --dir /path/to/hub
```

It exits 0 when nothing is named, or when the store is already carried over, and 1 when it names any record, so a deploy
script can gate on it. The hub's startup log carries the same finding as one warning per refusal,
`forge migration would refuse records`, naming the tenant, the case, and the slugs; it never stops the hub starting.

## How delivery uses a record

A `deliver` step resolves each of its chunk's commit pointers to its own repository record before its action takes a
pass. Each commit's `repo` is qualified to the record's `owner/repo`, and lands against that record's `forge` and
`owner`, on the chunk's effective branch for that repository. The `land` action reaches each repository through the
landing seam, an adapter for the record's forge kind built from the record and its secret, revealed once per distinct
secret for that pass and only for the repositories the pass needs a handle for, so the secret never leaves the hub.

A pointer matches a record by repo name, narrowed by the origin's owner and forge host when its URL names them; a
`file://` origin names neither and matches by bare name. A record counts for a chunk when it is enabled, or was retired
after the chunk was minted, so retiring a repository never strands work already under way. A chunk with no commits has
nothing to resolve and lands as a no-op.

The forge's unchanged answers are read conditionally; the shipped graphs poll once a minute. A pass may also compare the
submission and branch against the base to establish that no code remains to land.

Only the repositories the chunk's project links are candidates, so a new repository receives nothing until a project
links it ([projects.md](./projects.md#links)).

A chunk's **effective branch** for a repository is its project's link branch when the link names one
([projects.md](./projects.md#a-links-base-branch)), else the record's `base_branch`. The chunk captures it once: at its
claim, for every repository its project links, or at its first `deliver` step for a repository it has not captured yet —
a chunk claimed before the repository was linked, or by a hub release that captured none. From then on every lease,
retry, restart and landing of that chunk uses the captured branch, so an edit to the link's or the record's branch
reaches only chunks that have not yet captured that repository. `blizzard hub chunk get` and `GET /api/chunks/{id}` list
each repository's branch in `repository_targets`, marked `inherited` or `override`; `captured_at` is `null` while it is
a preview the next edit still moves. Chunks landing on different branches of one repository take turns on separate
flows, and chunks of any projects landing on the same branch share one ([flows.md](./flows.md)).

A runner starts a chunk on the same branches. Preparing an environment, it puts each worktree whose origin is a targeted
repository's `owner/repo` on that repository's branch and every other worktree on its `[[workspace]]` `base_branch`,
which stays the branch for worktrees no chunk target names; the worker's facts table names each targeted repository's
branch. A branch the repository's origin lacks fails the preparation, naming the repository and branch, and the chunk is
not claimed; so does a project link's own branch for a repository no worktree of the environment matches. The hub never
offers a chunk whose link names its own branch to a runner of a release that cannot prepare at it, and refuses a claim
whose environments were prepared at branches the chunk no longer targets, so the runner gives the environment back and
peeks again.

When no commits are declared, delivery completes with "no code to land" without opening a PR. If the base already
contains a declared commit and the branch head is safe, the repository resolves without opening a PR; the same proof is
checked after a refused open. This resolution is bound to its record, base, branch and submitted commit, so a later
changed declaration is reconsidered. A `fast-forward` repository whose base already holds the work resolves this way
too. A `park` repository applies the proof to fresh work only when its forge offers ref reads and comparison; otherwise
it opens its pull request and waits as usual. It does not create a `merged/<repo>` revision or a repository landing:
only an actual merge does. Mixed chunks record landings only for the repositories that merged. Work items close when the
chunk reaches `done`, including when nothing merged.

A forge that lacks an operation the action needs refuses the step too, escalating the chunk with
`forge-capability-missing`, whether the operation is required up front or found needed during the pass
([forges.md](./forges.md#a-missing-capability)).

Three outcomes refuse the step before it runs and route its failure choice; the first two also record an event
([observability.md](./observability.md)):

- `repository-unresolved` — no record stands for a pointer, or its name matches several records.
- `repository-not-linked` — a pointer resolves to a repository of the tenant that the chunk's project does not link:
  link it to the project, or decide the chunk's work strayed.
- `repository-secret-unavailable` — a record's secret is missing or retired; the log names the repository by slug.
  Replace or enable the secret.

Each repository lands by its own `landing_policy`. A chunk whose work reaches several repositories lands them in its
project's landing order: the repositories the project ranks first, lowest position first, then every unranked one in
slug order, so a project that ranks nothing lands by slug ([projects.md](./projects.md#landing-order)). A `deliver` step
takes the order and each repository's policy and `allow_no_ci` setting at its first turn and keeps them through retries
and hub restarts, so an edit made while it waits reaches the next step, not that one.

Records that differ on forge, owner, base branch or secret do not refuse the step. The `land` action lands each
repository against its own record and records each repository's landing, and the step refuses two commit entries that
share one `owner/repo`, such as the same name on two forge hosts, because they would share one `merged/<repo>` marker.

The hub reads records on every step, so an edit or a secret replace reaches the next step with no restart; a branch edit
reaches only chunks that have not captured that repository's branch. A hub whose environment still sets `BZ_FORGE_URL`,
`BZ_FORGE_TOKEN`, `BZ_FORGE_OWNER` or `BZ_FORGE_BASE_BRANCH` refuses to start;
[install.md](./install.md#work-sources-and-forge-settings-are-records) owns carrying them into records.
