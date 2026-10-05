# Configuration documents

A configuration document declares work sources and repositories in one YAML or JSON file. `blizzard hub config apply`
reconciles the hub's stored records to it in a single transaction, and `blizzard hub config export` writes the current
records back out as a document.

```bash
blizzard hub config apply config.yaml --dry-run
blizzard hub config apply config.yaml
blizzard hub config export --format json > config.json
```

## The document

```yaml
version: 1
secrets: [gh-token]
work_sources:
  - name: blizzard
    provider: github
    locator: paul-gross/blizzard
    secret: gh-token
    annotate: true
repositories:
  - name: blizzard
    forge_api_url: https://api.github.com
    owner: paul-gross
    repo: blizzard
    base_branch: master
    secret_name: gh-token
```

`version` must be `1`. Each entry carries the fields its own record takes, so [work sources](./work-sources.md) and
[repositories](./repositories.md) own the field meanings, and `GET /api/config/schema/work-sources` and `/repositories`
serve each entry's JSON Schema. An unknown field is refused. `secrets` lists secret names that must already exist and be
active; a document never carries a secret's value, so set one first with `blizzard hub secret set`. Routines and scopes
are not part of a document.

The file's extension picks the format: `.yaml` and `.yml` read as YAML, `.json` as JSON. Over HTTP the `Content-Type`
does: `POST /api/config/apply` takes `application/yaml` or `application/json`, and any other type is refused with 415.

## What an apply does

An apply looks at each work source and repository the document names:

| The record is…                        | The apply…                                           | Outcome row |
| ------------------------------------- | ---------------------------------------------------- | ----------- |
| Not stored                            | Creates it from the entry                            | `create`    |
| Stored, and differs in a stated field | Edits only the fields the entry states               | `edit`      |
| Retired                               | Enables it, then edits any stated field that differs | `enable`    |
| Stored, and already as declared       | Writes nothing                                       | `unchanged` |

A field the entry leaves out is neither compared nor changed, so a partial entry never reverts a field set elsewhere. A
record the document does not name is left alone, retired or not: an apply never retires a record. `enable` appears
before the `edit` it precedes, as two rows.

Every write commits in one transaction, so the first refusal leaves the hub exactly as it was. The refusals are the ones
the per-record verbs give, each located at its entry:

- **422** for an invalid entry, a missing or retired secret, a name listed twice, or a document that does not decode.
  The error's `loc` names the entry and field, such as `body.work_sources.1.provider`.
- **409** for a taken name, a taken provider and locator or forge coordinate, a record that changed while the apply ran,
  or an entry naming the built-in `hub` work source.

## Dry run

`--dry-run`, or `?dry_run=true` over HTTP, runs the same transaction and rolls it back. It reaches every refusal a real
apply reaches and prints the same outcome rows the real apply that follows would, and it writes no change rows.

The response lists `outcomes`, each with a `kind`, `key`, `op` and the changed fields. A real apply also returns an
`apply_id`; a dry run returns `null`.

## Export

`config export` prints every active work source and repository with every field, and every active secret name, as a
document that applies back as a no-op. `--format` is `yaml` (the default) or `json`. Retired records are left out,
because applying one would enable it again. The built-in `hub` work source is never exported.

## Recording

An apply writes its rows to the [change log](./config-changes.md) with the `apply` door, and every row one apply writes
carries the same `apply_id`. Applying needs the `config:edit` permission, and exporting needs `fleet:view`.
