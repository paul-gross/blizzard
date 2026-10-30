# Repositories

A hub holds the repositories work lands in as stored records, managed with `blizzard hub repo`. Each record names one
repository on a [forge](./forges.md) and the stored secret that authenticates against it, and has a slug you type, a
display name, and an id the hub mints:

```bash
printf '%s' "$TOKEN" | blizzard hub secret create gh-token
blizzard hub forge create github --kind github --api-base https://api.github.com
blizzard hub repo create blizzard --forge github --owner paul-gross --repo blizzard \
  --base-branch master --secret gh-token
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

| Field           | Meaning                                                                                      |
| --------------- | -------------------------------------------------------------------------------------------- |
| `slug`          | The handle you type. It may be edited; the one it leaves still resolves.                     |
| `name`          | The display name, free text. It may be edited.                                               |
| `forge`         | The forge it lives on, by id or slug; reads carry `forge_id`, `forge_slug` and `forge_name`. |
| `forge_api_url` | Deprecated. The forge's API origin, derived from `forge` on reads.                           |
| `owner`         | The repository's owner on the forge.                                                         |
| `repo`          | The repository's name on the forge.                                                          |
| `base_branch`   | The branch work lands on.                                                                    |
| `secret_name`   | The stored [secret](./secrets.md) holding the forge token (`--secret`).                      |

Every field but the deprecated `forge_api_url` is required, and none can be cleared. Each `(forge, owner, repo)` belongs
to one record. `create` takes exactly one of `forge` and `forge_api_url`; the old field is accepted with a warning until
a later release retires it, and [forges.md](./forges.md#the-old-address-fields) owns how it resolves.

### The forge token

On GitHub, delivery needs a fine-grained token on the repository with these permissions:

| Permission    | Access       | Why                                                                         |
| ------------- | ------------ | --------------------------------------------------------------------------- |
| Contents      | Read & write | Read and move branches, compare revisions                                   |
| Pull requests | Read & write | Open, update and merge pull requests                                        |
| Checks        | Read         | Read each check on a revision                                               |
| Actions       | Read & write | Re-run, once, a GitHub Actions check that is failing on the base branch too |

Without Actions write, an inherited failure is never re-run and routes as the change's own failure.

## Verbs

`create <slug>` stores the repository at revision 1, named `--name` (default: the slug). It is refused when another
repository holds the slug as its current one, when the forge, owner and repo are already taken, or when the secret is
missing or retired. Taking a slug another repository left moves it to the new repository. Every later change moves the
revision by one:

- `edit <repository>` changes only the flags given: `--slug`, `--name`, `--forge`, `--forge-api-url`, `--owner`,
  `--repo`, `--base-branch`, and `--secret`. An edit that leaves every field as it is writes nothing and keeps the
  revision.
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
`null`. The document schema is served at `GET /api/config/schema/repositories`.

The board's Admin page lists repositories, with each record's revision, last change, and history. On a desktop, a user
with `config:edit` can create, edit, retire, and enable them there, seeing the fields that will change before an edit
saves. On a phone the page is read-only and names the `blizzard hub repo` command that makes the change. The repository
form picks an enabled forge by name instead of editing an address. Version-2 configuration documents use the forge's
slug in `forge`; version-1 documents continue to use the deprecated `forge_api_url`.

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
pass. Each commit's `repo` is qualified to the record's `owner/repo`, and lands against that record's target: its
`forge`, `owner` and `base_branch`. The `land` action reaches each repository through the landing seam, an adapter for
the record's forge kind built from the record and its secret, revealed once per distinct secret for that pass, so the
secret never leaves the hub.

A pointer matches a record by repo name, narrowed by the origin's owner and forge host when its URL names them; a
`file://` origin names neither and matches by bare name. A record counts for a chunk when it is enabled, or was retired
after the chunk was minted, so retiring a repository never strands work already under way. A chunk with no commits has
nothing to resolve and lands as a no-op.

The forge's unchanged answers are read conditionally, so a landing that is waiting costs at most one counted call a
pass, and the shipped graphs poll once a minute.

Only the repositories the chunk's project links are candidates, so a new repository receives nothing until a project
links it ([projects.md](./projects.md#links)).

A forge that lacks an operation the action needs refuses the step too, escalating the chunk with
`forge-capability-missing` ([forges.md](./forges.md#a-missing-capability)).

Three outcomes refuse the step before it runs and route its failure choice; the first two also record an event
([observability.md](./observability.md)):

- `repository-unresolved` — no record stands for a pointer, or its name matches several records.
- `repository-not-linked` — a pointer resolves to a repository of the tenant that the chunk's project does not link:
  link it to the project, or decide the chunk's work strayed.
- `repository-secret-unavailable` — a record's secret is missing or retired; the log names the repository by slug.
  Replace or enable the secret.

Records that differ on forge, owner, base branch or secret do not refuse the step. The `land` action lands each
repository against its own record and records each repository's landing, and the step refuses two commit entries that
share one `owner/repo`, such as the same name on two forge hosts, because they would share one `merged/<repo>` marker.

The hub reads records on every step, so an edit or a secret replace reaches the next step with no restart. A hub whose
environment still sets `BZ_FORGE_URL`, `BZ_FORGE_TOKEN`, `BZ_FORGE_OWNER` or `BZ_FORGE_BASE_BRANCH` refuses to start;
[install.md](./install.md#work-sources-and-forge-settings-are-records) owns carrying them into records.
