# Configuration changes

Every committed write to a stored forge, work source, repository, secret, project, project link, scope, or routine
appends one row to the hub's change log, in the same transaction as the write. A write that changes nothing appends
nothing.

## Reading the log

```bash
blizzard hub config changes
blizzard hub config changes --kind work_source --key blizzard --all
blizzard hub config changes --kind forge --key github
```

Rows come newest first. Each carries the acting user, the door, the kind and key of the record, the record's revision
after the change, the operation, and the fields that changed. Without `--all` the verb prints the newest page; `--kind`
is `forge`, `work_source`, `repository`, `secret`, `scope`, `routine`, or `project`, and `--key` narrows to one record —
a scope's slug, a routine's `name`, or a project's, work source's or repository's id or slug. `--json` prints the raw
rows.

A forge's, a project's, a work source's and a repository's rows are keyed by id, as are a project link's rows by the
linked record's id, so they follow the record across slug changes; each row carries the record's current slug as
`record_label`, which the listing shows. A row's recorded fields stay as written: a slug edit lists the old and new
slug. For a changed `forge` field, `old` and `new` remain the stored forge ids. `old_label` and `new_label` give the
current slug and display name of each referenced forge; the board shows those labels in its revisions view. Historical
address field names remain as originally recorded.

Over HTTP the same log is `GET /api/config/changes?record_kind=&record_key=&project=&before=&limit=`, newest first, at
most 200 rows a page. Pass the response's `next_before` as `before` to read the next page; it is `null` on the last.
`project` keeps that project's changes and the tenant-wide ones. The response's `total` counts every change the filters
select, whatever `before`. Each row's `actor_name` is the actor's display name, or their username, and is null when the
actor is not a user. Reading needs the `fleet:view` permission.

The board's Admin page has a Changes tab that reads the same log, newest first, with each entry's author by name, door,
and fields, and the log's total as the tab's count. An older page loads on request.

## Operations

| Operation | Written when                 | Fields listed                                      |
| --------- | ---------------------------- | -------------------------------------------------- |
| `create`  | A record is stored           | Every set field, each against an old value of null |
| `edit`    | A record's fields change     | Only the fields whose values changed               |
| `retire`  | A record is retired          | `retired`, from false to true                      |
| `enable`  | A retired record is enabled  | `retired`, from true to false                      |
| `replace` | A secret's value is replaced | None                                               |

A routine's scope link or unlink is an `edit` whose changed field is `scopes`. A secret's value never appears in a row,
so a secret's `create` and `replace` list no fields. A secret's `retire` and `enable` record the secret's unchanged
revision, because only a replace moves it.

## Doors

The door names where the write came from: `cli` for `blizzard hub` verbs, `board` for the web board, and `api` for any
other client. A client may claim only `cli` or `board`; every other value, or none, is recorded as `api`. `apply` and
`migration` are reserved for the hub's own use and are never accepted from a caller.

`blizzard hub config apply` writes `apply` rows, and every row one apply writes shares an `apply_id`, so one apply reads
back as one group. [Configuration documents](./config-documents.md) owns what an apply writes.
`blizzard hub config
import-legacy` writes `migration` rows for the records it carries over from a file-configured hub.
