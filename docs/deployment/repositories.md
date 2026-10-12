# Repositories

A hub holds the repositories work lands in as stored records, managed with `blizzard hub repo`. Each record names one
repository on a forge and the stored secret that authenticates against it, and has a slug you type, a display name, and
an id the hub mints:

```bash
printf '%s' "$TOKEN" | blizzard hub secret create gh-token
blizzard hub repo create blizzard --forge-api-url https://api.github.com --owner paul-gross --repo blizzard \
  --base-branch master --secret gh-token
blizzard hub repo list
blizzard hub repo show blizzard
```

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

| Field           | Meaning                                                                  |
| --------------- | ------------------------------------------------------------------------ |
| `slug`          | The handle you type. It may be edited; the one it leaves still resolves. |
| `name`          | The display name, free text. It may be edited.                           |
| `forge_api_url` | The forge's API origin, an absolute `http` or `https` URL.               |
| `owner`         | The repository's owner on the forge.                                     |
| `repo`          | The repository's name on the forge.                                      |
| `base_branch`   | The branch work lands on.                                                |
| `secret_name`   | The stored [secret](./secrets.md) holding the forge token (`--secret`).  |

Every field is required, and none can be cleared. Each `(forge_api_url, owner, repo)` belongs to one record.

## Verbs

`create <slug>` stores the repository at revision 1, named `--name` (default: the slug). It is refused when another
repository holds the slug as its current one, when the forge, owner and repo are already taken, or when the secret is
missing or retired. Taking a slug another repository left moves it to the new repository. Every later change moves the
revision by one:

- `edit <repository>` changes only the flags given: `--slug`, `--name`, `--forge-api-url`, `--owner`, `--repo`,
  `--base-branch`, and `--secret`. An edit that leaves every field as it is writes nothing and keeps the revision.
- `retire <repository>` hides the repository from `list` unless `--include-retired` is given. A retired repository keeps
  its forge, owner and repo, so `create` for another slug on the same coordinate is refused with a message naming the
  holder; `enable` the holder instead.
- `enable <repository>` lifts the retirement, and is refused while the repository's secret is retired.

`show`, `edit`, `retire` and `enable` take the repository's id or its slug.

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
saves. On a phone the page is read-only and names the `blizzard hub repo` command that makes the change.

## How delivery uses a record

A `deliver` step resolves each of its chunk's commit pointers to its own repository record before any command runs. Each
commit's `repo` is qualified to the record's `owner/repo`, and its entry carries that record's target: `forge_url` from
`forge_api_url`, `owner`, `base_branch`, and the `record` id. `BZ_FORGE_TOKENS` maps each record id to its secret,
revealed once per distinct secret for that step. When every pointer's record agrees on forge, owner, base branch and
secret, the step also gets the single-valued `BZ_FORGE_URL`, `BZ_FORGE_OWNER`, `BZ_HUB_BASE_BRANCH` and
`BZ_FORGE_TOKEN`; when they differ, those four are unset and whether the mix is acceptable is the land script's call.

A pointer matches a record by repo name, narrowed by the origin's owner and forge host when its URL names them; a
`file://` origin names neither and matches by bare name. A record counts for a chunk when it is enabled, or was retired
after the chunk was minted, so retiring a repository never strands work already under way. A chunk with no commits has
nothing to resolve and lands as a no-op.

Only the repositories the chunk's project links are candidates, so a new repository receives nothing until a project
links it ([projects.md](./projects.md#links)).

Three outcomes refuse the step before it runs and route its failure choice; the first two also record an event
([observability.md](./observability.md)):

- `repository-unresolved` — no record stands for a pointer, or its name matches several records.
- `repository-not-linked` — a pointer resolves to a repository of the tenant that the chunk's project does not link:
  link it to the project, or decide the chunk's work strayed.
- `repository-secret-unavailable` — a record's secret is missing or retired; the log names the repository by slug.
  Replace or enable the secret.

Records that differ on forge, owner, base branch or secret do not refuse the step. The shipped land scripts land each
repository against its own record, and refuse two commit entries that share one `owner/repo`, such as the same name on
two forge hosts, because they would share one `merged/<repo>` marker.

The hub reads records on every step, so an edit or a secret replace reaches the next step with no restart. A hub whose
environment still sets `BZ_FORGE_URL`, `BZ_FORGE_TOKEN`, `BZ_FORGE_OWNER` or `BZ_FORGE_BASE_BRANCH` refuses to start;
[install.md](./install.md#work-sources-and-forge-settings-are-records) owns carrying them into records.
