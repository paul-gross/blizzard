# Configuration documents

A configuration document declares forges, work sources, repositories, scopes, and routines in one YAML or JSON file.
`blizzard hub config apply` reconciles the hub's stored records to it in a single transaction, and
`blizzard hub config export` writes the current records back out as a document.

```bash
blizzard hub config apply config.yaml --dry-run
blizzard hub config apply config.yaml
blizzard hub config export --format json > config.json
blizzard hub config export --version 1 > legacy.yaml
```

## The document

```yaml
version: 2
forges:
  - slug: github
    kind: github
    api_base: https://api.github.com
secrets: [gh-token]
work_sources:
  - slug: blizzard
    name: Blizzard
    provider: github
    locator: paul-gross/blizzard
    forge: github
    secret: gh-token
    annotate: true
repositories:
  - slug: blizzard
    name: Blizzard
    forge: github
    owner: paul-gross
    repo: blizzard
    base_branch: master
    secret_name: gh-token
projects:
  - slug: blizzard
    name: Blizzard
    sources: [blizzard]
    repositories: [blizzard]
scopes:
  - slug: blizzard
    project: blizzard
    description: The blizzard repository
routines:
  - name: architecture-drift
    graph_name: garden-routine
    default_scope_slug: blizzard
    default_model: [claude-opus]
    project: blizzard
    scopes: [blizzard]
```

`version` is `1` or `2`. Version 2 declares forges and names them in repository and work-source entries; version 1
retains its address fields. Each entry carries the fields its own record takes, so [work sources](./work-sources.md),
[repositories](./repositories.md), and [scopes and routines](./routines-and-scopes.md) own the field meanings, and
`GET /api/config/schema/work-sources`, `/repositories`, `/scopes`, and `/routines` serve each entry's JSON Schema. Pass
`?version=2` for the new entry shapes and `/api/config/schema/forges?version=2` for forge entries; the default is
version 1. An unknown field is refused. `secrets` lists secret names that must already exist and be active; a document
never carries a secret's value, so set one first with `blizzard hub secret create`.

A scope entry is keyed by its `slug`, a routine entry by its `name`. A routine's `scopes` states its linked scope set,
its default scope always among it; an entry that leaves `scopes` out keeps the stored set. Every scope a routine entry
names, as its `default_scope_slug` or in `scopes`, must be stored already or declared in the same document's `scopes`
section, and its `graph_name` must name an enabled graph when the entry creates the routine or moves it to another
graph; otherwise the entry is refused. An apply never creates a scope the document does not declare.

A work source entry and a repository entry are each keyed by its `slug`; its `name` is the display name, the slug when a
new record's entry leaves it out and as stored when an existing record's does. An entry without a `slug` is refused
`422` naming the field: the key that was a work source's or a repository's `name` is its `slug` now. A slug a record of
the same kind has left is refused `422`, naming that record: name it by its current slug
([work-sources.md](./work-sources.md#naming-a-work-source), [repositories.md](./repositories.md#naming-a-repository)). A
work source entry whose slug is `hub`, the built-in source's, is refused `422` at its `slug`.

A version-2 forge entry is keyed by `slug` and carries `kind`, `api_base`, and optional `name` and `web_base`. The kind
of an existing forge cannot change. A slug another forge left is refused, naming its holder. Forge entries reconcile
after the secret check and before sources and repositories, so those entries may name a forge declared in the same file.

A project entry is keyed by its `slug`; one the document creates needs a `name`. A `color` it states is stored, and one
it leaves out keeps the stored color, or gives a created project the tenant's least-used one
([projects.md](./projects.md#a-projects-color)). A `sources` or `repositories` list the entry states is the project's
whole set of links of that kind, so a name it leaves out is unlinked; a list it leaves out keeps the stored links
([projects.md](./projects.md#links)). A project names a work source or a repository by its id, its slug, or a slug the
record left, among the records of that kind the apply leaves — those stored and those the document declares. A slug a
project has left is refused `422`, naming that project: name it by its current slug. A scope or routine entry names its
project with `project`, an id or a slug; an entry without one belongs to the tenant's only project, and is refused
`project_required` when the tenant holds several.

The file's extension picks the format: `.yaml` and `.yml` read as YAML, `.json` as JSON. Over HTTP the `Content-Type`
does: `POST /api/config/apply` takes `application/yaml` or `application/json`, and any other type is refused with 415.

## What an apply does

An apply looks at each record the document names, forges before work sources and repositories, and scopes before
routines:

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

- **422** for an invalid entry, a missing or retired secret, a name listed twice, a work source slugged `hub`, or a
  document that does not decode. The error's `loc` names the entry and field, such as `body.work_sources.1.provider`.
- **409** for a taken name, a taken provider and locator or forge coordinate, or a record that changed while the apply
  ran.

## Dry run

`--dry-run`, or `?dry_run=true` over HTTP, runs the same transaction and rolls it back. It reaches every refusal a real
apply reaches and prints the same outcome rows the real apply that follows would, and it writes no change rows.

The response lists `outcomes`, each with a `kind`, `key`, `op` and the changed fields. A real apply also returns an
`apply_id`; a dry run returns `null`.

## Export

`config export` prints every active forge, work source, repository, scope, and routine with every field, and every
active secret name, as a document that applies back as a no-op. `--format` is `yaml` (the default) or `json`. Retired
records are left out, because applying one would enable it again. Each work source and repository is written with its
`slug` and `name`, and each project's `color`, `sources` and `repositories` as slugs. The built-in `hub` work source is
never exported.

The CLI exports version 2 by default; `--version 1` exports the old address-based shape. `GET /api/config/export`
defaults to version 1 for existing clients; pass `?version=2` for the forge-based shape. In version 2, `forges` follows
`projects`, references use forge slugs instead of address fields, and even an unreferenced active forge is exported. A
version-2 export applies back unchanged.

## Recording

An apply writes its rows to the [change log](./config-changes.md) with the `apply` door, and every row one apply writes
carries the same `apply_id`. Applying needs the `config:edit` permission, and exporting needs `fleet:view`.
