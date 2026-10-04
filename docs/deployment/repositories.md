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

| Field           | Meaning                                                                    |
| --------------- | -------------------------------------------------------------------------- |
| `name`          | The record's handle. It never changes.                                     |
| `forge_api_url` | The forge's API origin, an absolute `http` or `https` URL.                 |
| `owner`         | The repository's owner on the forge.                                       |
| `repo`          | The repository's name on the forge.                                        |
| `base_branch`   | The branch work lands on.                                                  |
| `secret_name`   | The stored [secret](./secrets.md) holding the forge token (`--secret`).    |

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

## What a record does not change yet

A stored repository does not yet change where the hub delivers. Delivery still takes its forge, owner, base branch, and
token from the hub's `BZ_FORGE_*` settings described in [install.md](./install.md).
