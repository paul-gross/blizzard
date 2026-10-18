# Change notes

A change note is a small Markdown file committed beside the change it describes. `blizzard-change-notes check` (run by
`mise run change-notes`, `mise run gate` and the `gate / change notes` check) refuses a malformed one and names the file
and the rule it broke.

## Where notes live

| Path                         | Holds                                                            |
| ---------------------------- | ---------------------------------------------------------------- |
| `changes/unreleased/<id>.md` | A note no release has carried. `unreleased/` ships a `.gitkeep`. |
| `changes/<X.Y.Z>/<id>.md`    | A note a release carried. Written only when a release is cut.    |
| `changes/README.md`          | This reference.                                                  |

`X.Y.Z` is a version core without leading zeros. A release candidate shares its version's directory: `v0.8.0-rc.1` and
`v0.8.0` both use `changes/0.8.0/`. Any other file or directory under `changes/` fails.

## The id

The id is the file stem. It matches `^[a-z0-9]+(-[a-z0-9]+)*$`, is at most 60 characters, and is unique across the whole
of `changes/`, released directories included.

## Frontmatter

The file opens with a `---` delimited YAML block. A repeated key and an unknown field both fail.

| Field           | Rule                                                                                                                            |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `kind`          | Required: `added`, `changed`, `deprecated`, `removed`, `fixed` or `security`.                                                   |
| `surfaces`      | Distinct values from `http-api`, `wire`, `config`, `store`, `trace`, `egress`, `cli`, `board`.                                  |
| `breaking`      | `true` or `false`, default `false`. `true` only on `changed` or `removed`.                                                      |
| `breaks`        | A list of strings naming what breaks. Only on a note with `breaking: true`.                                                     |
| `removes-in`    | A quoted major (`"2"`) or pre-1.0 minor (`"0.12"`). Required on `deprecated` and on a note that `postpones`; refused elsewhere. |
| `postpones`     | The id of a deprecation note. Only on `changed`.                                                                                |
| `retires`       | The id of a deprecation note. Only on `removed`.                                                                                |
| `upgrade-guide` | An anchor of a heading in `docs/upgrade.md`, without the `#`, as GitHub renders it.                                             |
| `flag`          | Refused: it waits for the flag registry.                                                                                        |

`retires` and `postpones` must name a `deprecated` note under a released `changes/<X.Y.Z>/` directory; an unreleased
note, or a note of another kind, does not count.

## The body

The body is non-empty prose. The only heading allowed is `## Upgrade`, at most once, with a non-empty section. It is
required when `breaking` is `true` or `kind` is `removed`. A line inside fenced code is not a heading.

## Examples

One per kind.

```markdown
---
kind: added
surfaces: [cli]
---

Adds `blizzard runner status`, which prints the runner's current lease.
```

```markdown
---
kind: changed
surfaces: [http-api, wire]
breaking: true
breaks:
  - The chunk list response no longer carries `legacy_state`.
upgrade-guide: host-graduation-boundary
---

The chunk list response drops `legacy_state`; read `state` instead.

## Upgrade

Switch any client that reads `legacy_state` to `state` before upgrading the hub.
```

```markdown
---
kind: deprecated
surfaces: [config]
removes-in: "2"
---

The `[legacy]` section of the hub configuration is deprecated; use `[hub]`.
```

```markdown
---
kind: removed
surfaces: [config]
retires: drop-legacy-section
---

Removes the `[legacy]` section of the hub configuration.

## Upgrade

Move every setting from `[legacy]` into `[hub]`.
```

```markdown
---
kind: fixed
surfaces: [board]
---

The board no longer shows a finished chunk as running after a reload.
```

```markdown
---
kind: security
surfaces: [http-api]
---

The hub now rejects a token whose signature algorithm is `none`.
```
