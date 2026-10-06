# Repositories

A hub holds the repositories work lands in as stored records, managed with `blizzard hub repo`. Each record names one
repository on a forge and the stored secret that authenticates against it:

```bash
printf '%s' "$TOKEN" | blizzard hub secret set gh-token
blizzard hub repo create blizzard --forge-api-url https://api.github.com --owner paul-gross --repo blizzard \
  --base-branch master --secret gh-token
blizzard hub repo list
blizzard hub repo show blizzard
```

## Fields

| Field           | Meaning                                                                 |
| --------------- | ----------------------------------------------------------------------- |
| `name`          | The record's handle. It never changes.                                  |
| `forge_api_url` | The forge's API origin, an absolute `http` or `https` URL.              |
| `owner`         | The repository's owner on the forge.                                    |
| `repo`          | The repository's name on the forge.                                     |
| `base_branch`   | The branch work lands on.                                               |
| `secret_name`   | The stored [secret](./secrets.md) holding the forge token (`--secret`). |

Every field is required, and none can be cleared. Each `(forge_api_url, owner, repo)` belongs to one record.

## Verbs

`create` stores the repository at revision 1. It is refused when the name or the forge, owner and repo are already
taken, or when the secret is missing or retired. Every later change moves the revision by one:

- `edit <name>` changes only the flags given: `--forge-api-url`, `--owner`, `--repo`, `--base-branch`, and `--secret`.
  An edit that leaves every field as it is writes nothing and keeps the revision.
- `retire <name>` hides the repository from `list` unless `--include-retired` is given. A retired repository keeps its
  forge, owner and repo, so `create` for another name on the same coordinate is refused with a message naming the
  holder; `enable` the holder instead.
- `enable <name>` lifts the retirement, and is refused while the repository's secret is retired.

`edit`, `retire`, and `enable` take `--if-match <revision>` and are refused, naming the current revision, when the
repository has moved on. An active repository keeps its secret from being retired. Every write is recorded in the
[change log](./config-changes.md).

Writes need the `config:edit` permission and reads need `fleet:view`. Over HTTP the verbs are `GET` and `POST`
`/api/repositories`, `GET` and `PATCH` `/api/repositories/{name}`, and `POST /api/repositories/{name}/retire` and
`/enable`. `PATCH` applies only the fields present and refuses an explicit `null`. The document schema is served at
`GET /api/config/schema/repositories`.

The board's Admin page lists repositories, with each record's revision, last change, and history. On a desktop, a user
with `config:edit` can create, edit, retire, and enable them there, seeing the fields that will change before an edit
saves. On a phone the page is read-only and names the `blizzard hub repo` command that makes the change.

## How delivery uses a record

A `deliver` step resolves its chunk's commit pointers to repository records before any command runs, and fills the forge
variables of its environment from the record: `BZ_FORGE_URL` from `forge_api_url`, `BZ_FORGE_OWNER` from `owner`,
`BZ_HUB_BASE_BRANCH` from `base_branch`, and `BZ_FORGE_TOKEN` from the secret, revealed for that step. Each commit's
`repo` is qualified to the record's `owner/repo`.

A pointer matches a record by repo name, narrowed by the origin's owner and forge host when its URL names them; a
`file://` origin names neither and matches by bare name. A record counts for a chunk when it is enabled, or was retired
after the chunk was minted, so retiring a repository never strands work already under way. A chunk with no commits has
nothing to resolve and lands as a no-op.

Two outcomes refuse the step before it runs, route its failure choice, and record an event
([observability.md](./observability.md)):

- `repository-unresolved` — no record stands for a pointer, or its name matches several records.
- `repositories-disagree` — the pointers resolve to records that differ on forge, owner, base branch or secret, so there
  is no single landing target.

The hub reads records on every step, so an edit or a secret replace reaches the next step with no restart. A hub whose
environment still sets `BZ_FORGE_URL`, `BZ_FORGE_TOKEN`, `BZ_FORGE_OWNER` or `BZ_FORGE_BASE_BRANCH` refuses to start;
[install.md](./install.md#work-sources-and-forge-settings-are-records) owns carrying them into records.
