# Change notes

A change note is a small Markdown file committed beside the change it describes. `blizzard-change-notes check` (run by
`mise run change-notes`, `mise run gate` and the `gate / change notes` check) refuses a malformed one and names the file
and the rule it broke; `blizzard-change-notes owed --against <ref>` (run by `mise run change-notes` and, on pull
requests, `gate / change notes`) fails a change that owes a note it does not add.

## Where notes live

| Path                         | Holds                                                             |
| ---------------------------- | ----------------------------------------------------------------- |
| `changes/unreleased/<id>.md` | A note no release has carried. `unreleased/` ships a `.gitkeep`.  |
| `changes/<X.Y.Z>/<id>.md`    | A note a release carried. Written by `blizzard-change-notes cut`. |
| `changes/README.md`          | This reference.                                                   |

`X.Y.Z` is a version core without leading zeros. A release candidate shares its version's directory: `v0.8.0-rc.1` and
`v0.8.0` both use `changes/0.8.0/`. Any other file or directory under `changes/` fails.

## Cutting and rendering

`blizzard-change-notes cut <X.Y.Z>` runs at a release's cut. It refuses on any `check` failure, and otherwise moves
every note in `changes/unreleased/` whose flag is on (or has no flag) into `changes/<X.Y.Z>/`, adding to the directory
when a candidate already made it. It prints notes held because their flag's registry default is off; a later cut moves
them when that default turns on. It also moves each `docs/upgrade.md` section the moved notes' `upgrade-guide` names
from `## Upgrading to the next release` into `## Upgrading to <X.Y.Z>`, creating that heading directly beneath
`## Before you pull` when it is absent. A cut that would leave an `upgrade-guide` on another heading refuses and writes
nothing. It also refuses a new deprecation promised for the cut's line or earlier, or a postponement that does not move
the promise beyond its latest released line. At a line-opening cut, every promise due on that line must have a retiring
removal in that line's notes or a postponement in the cut. It also runs `blizzard-compat --tag <X.Y.Z>` and refuses when
it exits non-zero or lets through a break that no breaking note in the prospective `changes/<X.Y.Z>/` names by surface
and `where`. A refusal names the offending notes and writes nothing; `check` still checks format and references, not
whether a release is ready to cut.

`blizzard-change-notes guide <line>` previews the crossing into a major (`1`) or pre-1.0 minor (`0.12`). It reads
released notes and pending removals and breaking notes whose flags are on (or absent) from `changes/unreleased/`.
Deprecations appear in the order their releases carried them, followed by their retiring removal's prose and
`## Upgrade` instructions and any named boundary link; unrelated breaking notes follow. It refuses by name when a
promise due on that line has no retiring removal in that line or in `unreleased/`. A released postponement moves the
promise's current line; an unreleased postponement does not change the guide until it is cut.

`blizzard-change-notes render <tag> [--breaks compat-breaks.json]`, or `mise run release-notes <tag>`, prints the notes
a tag publishes, read from the tag's own tree: an opening release (`X.0.0`, or a `0.Y.0` only when some deprecation is
due to retire in `0.Y`) leads with **Upgrading to <line>**, followed by upgrade notes, flags newly on since the previous
release under **Turned on** by registry summary, then breaking changes, deprecations, and the added, changed, fixed and
security notes, then the commits since the previous tag in a collapsed block. The **Deprecated** section shows each
promise's current line after released postponements. A note that an earlier release on another line also carries is
marked `(also in X.Y.Z)`, for the releases `blizzard-compat releases --json` lists as supported at the checkout
(re-render an old tag from that tag's checkout). With `--breaks`, rendering repeats the cut's match and fails on a
recorded break no note names; **Upgrade notes** then ends with one line per recorded break beside the note that names
it, and a declared break value that compat does not record goes to stderr without failing. Rendering a tag twice gives
the same text.

## When a note is owed

`blizzard-change-notes owed --against <ref>` judges the commits in `merge-base(HEAD, <ref>)..HEAD` and prints every
waiver it honours, then each failure with its rule and remedy.

- **A contract document changed.** A range that changes an OpenAPI document, a configuration, trace, egress or CLI
  contract document adds or edits a note under `changes/unreleased/`. The store schema documents are left out. A change
  an operator would not notice, such as a reordered enum or a corrected description, waives this with a trailer on any
  commit of the range: `Change-Note: none — <reason>`. The reason is required, after the em dash. A release cut, the
  range with a `Release-Cut:` trailer, is exempt.
- **`!` and `breaking: true` travel together.** A commit with the `!` marker needs an added note with `breaking: true`
  and an `## Upgrade` section, and an added note with `breaking: true` needs a `!` commit. Editing an existing note to
  `breaking: true` counts as adding one. No trailer waives this.
- **Released notes stay put.** A range does not add, delete or rename a note under `changes/<X.Y.Z>/` or change its
  frontmatter; a body edit is fine. A `Release-Cut: <X.Y.Z>` commit may write `changes/<X.Y.Z>/`.

## The id

The id is the file stem. It matches `^[a-z0-9]+(-[a-z0-9]+)*$`, is at most 60 characters, and is unique across the whole
of `changes/`, released directories included.

## Frontmatter

The file opens with a `---` delimited YAML block. A repeated key and an unknown field both fail.

| Field           | Rule                                                                                                                                                    |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `kind`          | Required: `added`, `changed`, `deprecated`, `removed`, `fixed` or `security`.                                                                           |
| `surfaces`      | Distinct values from `http-api`, `wire`, `config`, `store`, `trace`, `egress`, `cli`, `board`.                                                          |
| `breaking`      | `true` or `false`, default `false`. `true` only on `changed` or `removed`.                                                                              |
| `breaks`        | A list of strings naming what breaks; the cut and `render --breaks` match one equal to a `blizzard-compat` finding's `where`. Only on `breaking: true`. |
| `removes-in`    | A quoted major (`"2"`) or pre-1.0 minor (`"0.12"`). Required on `deprecated` and on a note that `postpones`; refused elsewhere.                         |
| `postpones`     | The id of a deprecation note. Only on `changed`.                                                                                                        |
| `retires`       | The id of a deprecation note. Only on `removed`.                                                                                                        |
| `upgrade-guide` | An anchor of a heading in `docs/upgrade.md`, without the `#`, as GitHub renders it.                                                                     |
| `flag`          | A live name in `src/blizzard/foundation/flags/registry.toml`; retired and unknown names are refused on unreleased notes.                                |

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
