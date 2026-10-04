# Configuration changes

Every committed write to a stored work source or secret appends one row to the hub's change log, in the same transaction
as the write. A write that changes nothing appends nothing.

## Reading the log

```bash
blizzard hub config changes
blizzard hub config changes --kind work_source --key blizzard --all
```

Rows come newest first. Each carries the acting user, the door, the kind and key of the record, the record's revision
after the change, the operation, and the fields that changed. Without `--all` the verb prints the newest page; `--kind`
is `work_source` or `secret`, and `--key` narrows to one record. `--json` prints the raw rows.

Over HTTP the same log is `GET /api/config/changes?record_kind=&record_key=&before=&limit=`, newest first, at most 200
rows a page. Pass the response's `next_before` as `before` to read the next page; it is `null` on the last. Reading
needs the `fleet:view` permission.

## Operations

| Operation | Written when                  | Fields listed                                      |
| --------- | ----------------------------- | -------------------------------------------------- |
| `create`  | A record is stored            | Every set field, each against an old value of null |
| `edit`    | A work source's fields change | Only the fields whose values changed               |
| `retire`  | A record is retired           | `retired`, from false to true                      |
| `enable`  | A retired record is enabled   | `retired`, from true to false                      |
| `replace` | A secret's value is replaced  | None                                               |

A secret's value never appears in a row, so a secret's `create` and `replace` list no fields. A secret's `retire` and
`enable` record the secret's unchanged revision, because only a replace moves it.

## Doors

The door names where the write came from: `cli` for `blizzard hub` verbs, `board` for the web board, and `api` for any
other client. A client may claim only `cli` or `board`; every other value, or none, is recorded as `api`. `apply` and
`migration` are reserved for the hub's own use and are never accepted from a caller.
