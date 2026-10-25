# Versioning

Blizzard follows [Semantic Versioning](https://semver.org/) as `MAJOR.MINOR.PATCH`, with `MAJOR` pinned at `0` until the
project reaches 1.0 — so under semver's own pre-1.0 carve-out a `MINOR` bump may carry a breaking change.

These rules bind what a release shipped. An `edge` or `sha-` image, a dev wheel, or a wheel built from a source checkout
carries no promise; `0.2.0` is the first release that does.

## The train

Releases are cut on a weekly train that runs on Monday, UTC. A train does two things, in order: it promotes the
candidate cut a week before, if one is waiting, and it cuts the next candidate, if `master` moved since the last one. An
operator may hold a train, skipping that week, but never runs two in one week; a held week is noted in the next
candidate's notes.

| Thing             | Spelling          | Cut from                                              |
| ----------------- | ----------------- | ----------------------------------------------------- |
| Release branch    | `release/X.Y`     | the minor's cut commit on `master`                    |
| Candidate         | `vX.Y.0-rc.K`     | `release/X.Y`, `K` counting from 1                    |
| Stable minor      | `vX.Y.0`          | the commit its newest candidate was tagged at         |
| Patch             | `vX.Y.P`, `P ≥ 1` | `release/X.Y`                                         |
| Major's candidate | `vX.0.0-rc.K`     | `master` while it is open for `X`, then `release/X.0` |

A candidate runs on blizzard's own fleet for the week before it is promoted. A fix the week finds is a further candidate
on the same branch, `vX.Y.0-rc.(K+1)`, and does not restart the week. A candidate is promoted when its train day comes,
its `release` run is green, and no open `release-blocker` issue on `paul-gross/blizzard` names its line. The stable
release is the same commit as the candidate, rebuilt with its stable version stamped; nothing in it is new code. A
candidate with an open blocker is set aside instead: its minor never has a stable release, its GitHub Release says so
and names the minor that supersedes it, and version numbers skip it. A branch is never deleted, and a tag, once pushed,
is never moved, deleted, or reused. [What a tag publishes](#what-a-tag-publishes) owns the ancestry check and the
`pyproject.toml` version, and [`docs/ci.md`](./ci.md) owns the proof a stable tag borrows from its candidate.

A minor counts trains, not milestones: it says when a release was cut and nothing about how much changed in it.

## Support

A **line** is a minor's release branch once that minor has a stable release. Only some lines receive patches:

| When                     | Lines patched                                                                                    |
| ------------------------ | ------------------------------------------------------------------------------------------------ |
| Before 1.0               | The newest stable minor alone                                                                    |
| Within a major, past 1.0 | The newest stable minor of the newest major                                                      |
| After a new major ships  | Also the previous major's final minor, for three months from the new major's `.0`, critical only |

A candidate is not a line, and a set-aside minor never is. The closing date of an old major's three months is written
out here the day the next major ships; after it that line receives nothing more.

A patch is for what cannot wait the week, and *critical* means the same three things on every line: a hole in security,
data lost or corrupted, or a fleet that has stopped working. Anything else rides the next train. A patch carries the
fix, its tests, and its change note alone: it adds no surface, turns no flag on, and carries no store revision
(`bzh:patch-scope`), so moving onto a patch, or back off it, needs no `migrate`.

## Schedule

No major date is published yet. 1.0 is not on the six-month cadence; the cadence counts from it. From 1.0, two majors a
year ship on dates published here, each date published no later than the day the major before it ships, and each on a
train day. A date moves only by being republished later with its reason, and never earlier.

## What counts as breaking

| Surface         | A release breaks it when it…                                                                                                                                                                                 |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| HTTP API        | removes a route, adds a required field to a request, removes a response field or changes its meaning, or adds a response enum value a release's clients read closed                                          |
| hub↔runner wire | is not wire-compatible with a runner inside the [skew window](#the-hubrunner-skew-window) — an `/api/fleet/...` route, or a field the runner's `IHubClient` (`src/blizzard/runner/hub/client.py`) depends on |
| Configuration   | renames or removes a `blizzard-hub.toml` or `blizzard-host.toml` key, or moves or removes a durable path under the runtime root                                                                              |
| Store schema    | ships a revision that cannot be walked back — breaking regardless of what else the release changed                                                                                                           |
| Trace contract  | renames or removes a span name, event name or attribute, changes an attribute's type or meaning, or changes how trace and span ids are derived                                                               |
| Egress contract | removes, renames or retypes a column of an exported dataset, or changes a column's meaning                                                                                                                   |

[`docs/backup.md`](./backup.md) owns the current durable layout. Every schema revision blizzard has ever shipped keeps a
working `downgrade()`, held mechanically for every revision in the tree by
`tests/test_store_migrations.py::test_migrate_up_and_down`.

Response enums are open from `0.3.0`, the first release whose clients tolerate a value they do not know; `0.2.0`'s
clients do not. A consumer of a response must tolerate an unknown enum value — show it, skip it, or defer to the
server's own state — and never fail on it. A response enum gaining a value is additive against a release whose own
OpenAPI document marks every response position reaching that enum open (`x-blizzard-open`), and breaking against any
other release, `0.2.0` included. Removing a value, a request enum losing a value, changing a value's meaning, a
declared-closed field gaining a value, a closed response enum widened to a plain string, and a response union gaining a
member all stay breaking against every release.

Adding an optional config key, a new route, a new event type, a new trace attribute or span, a new nullable egress
column, a new value of an enumerated egress column, a new value of a response enum every supported release reads open,
or a migration that walks back cleanly is not breaking.

A release carries a surface whether or not a feature flag has it on, so it is held to these rules from that release on.

Mark a breaking commit with a `!` before the colon of its Conventional Commit subject: `feat!: ...`,
`feat(scope)!: ...`.

## The configuration contract

[`contracts/config/`](../contracts/config/README.md) lists every key each daemon reads from its toml file — `hub.json`
for `blizzard-hub.toml`, `host.json` for `blizzard-host.toml` — and every durable path under its runtime root.
`blizzard:config-contract` and the merge gate's contract drift step fail when the code and the documents disagree, so
adding, retiring or retyping a key is always a deliberate edit to the document.

A key marked `deprecated` is still read, or still refused with a message naming its home; `replaced_by` names the key or
table that took its place, and is `null` when nothing did. Removing a deprecated key is the breaking change.

Surface behind a feature flag that is off is already released and held to all of this; [`flags.md`](./flags.md) owns
what a flag and its `[flags]` override are.

## The trace contract

[`contracts/traces/`](../contracts/traces/README.md) pins the shape of a chunk's traces — the work root, its step roots
and the runner leases under them, and the lifetime trace: `dictionary.json` is the authored contract, and `golden/` is
the spans the hub and a runner assemble for seeded scenarios. `blizzard:trace-contract` fails when the assembled spans
drift from either, so a shape change is always a deliberate edit to the dictionary.

A rename is not a single release: the release that introduces the new name emits both, for at least one minor, with the
old name marked deprecated in `dictionary.json`. Removing the old name afterwards is the breaking change. The exception
is a breaking schema raise whose new shape cannot carry both names, such as one that moves a name to a different span:
it may rename or reassign a span name with no deprecation period, and its upgrade note names the change. Every breaking
trace change raises `blizzard.trace.schema_version` and the instrumentation scope version together, so a backend can
tell shapes apart without reading the release notes. [`docs/deployment/tracing.md`](./deployment/tracing.md) describes
the shape for operators. A raised schema version comes with an upgrade note; schema 3's is
[Upgrading from trace schema 2](./deployment/tracing.md#upgrading-from-trace-schema-2), and schema 2's is
[Upgrading from trace schema 1](./deployment/tracing.md#upgrading-from-trace-schema-1).

## The egress contract

[`contracts/egress/`](../contracts/egress/README.md) pins the shape of the `steps`, `invocations` and `events` datasets
and the views over them that the hub exports: `dictionary.json` is the authored contract, and `golden/` and `_schema/`
are generated from it. `blizzard:egress-contract` fails when the code, the writer's output, `_schema/` or the published
dictionary in [`docs/deployment/egress.md`](./deployment/egress.md) drift from it, so a shape change is always a
deliberate edit to the dictionary.

- **Additive.** A new nullable column, or a new value of an enumerated column, keeps the major version. A consumer must
  tolerate values an open column does not list, and older files that lack a column added since. The export keeps writing
  under the same major version and widens the dataset's `_schema/` document in place
  ([The directory](./deployment/egress.md#the-directory)).
- **Breaking.** Removing, renaming or retyping a column, or changing a meaning, writes a new major version beside the
  old (`steps/v2/`). Both are written for at least one minor release before the old one stops, as with a trace rename.
  Stopping the old major is the breaking change, and carries the `!` marker.

## The hub↔runner skew window

A hub accepts runners of any line from the window's `floor` up to its own, at any patch, and never requires a runner
newer than itself; the hub is upgraded first and its runners after it. The `floor` is declared in
[`src/blizzard/wire/window.toml`](../src/blizzard/wire/window.toml).

Before 1.0, the `floor` rises only in the change that removes surface a released minor deprecated, to the line that
deprecated it, so the window spans every minor since the last removal. From 1.0, a runner works against any hub of its
own major at the same minor or later, and a hub of a new major also accepts runners at the previous major's final minor,
which is then the `floor`.

At runtime, every registration carries the runner's version and the hub judges it against the window: a runner outside
it is refused `409 RunnerVersionRefusal`, whose `detail` names the runner's version, the hub's, and the range to upgrade
to, and is denied every claim until a later registration is admitted. The refused runner halts alone while its host's
other runners carry on, and retries registration on its tick; its panel's host page shows the refusal
([`docs/remote-runner.md`](./remote-runner.md)); a hub whose own line is below `floor` refuses to start. What is checked
is every committed surface document against every supported release: `blizzard:compat` (`bzh:released-surface-additive`)
fails a pull request or a push that breaks one unless the break is one the window admits, or one a recorded allowance
admits because that release line's consumers survive it ([`contracts/compat/README.md`](../contracts/compat/README.md)),
and `release.yml` runs it again at a tag. [`docs/ci.md`](./ci.md) owns where each run happens.

A vocabulary field a response carries is open: a client reads a value outside the vocabulary it knows as unrecognized
and carries the raw string on, instead of failing. A response field is declared closed only where acting without knowing
the value would do wrong work, as for the fields a runner dispatches a node-step on; a gained value there is breaking. A
field a server takes as input stays closed, so the server refuses a value it does not know with `422`. That includes a
class used in both directions: `TurnSegmentView.kind` (`src/blizzard/wire/transcript_segment.py`) is a closed
`TurnKind`, because a runner ships transcript segments to the hub. A runner sending a kind an older hub does not know
fails the whole ingest batch, so adding a value to `TurnKind` is breaking against the skew window, not additive.

The capability-matched fleet peek sits on the additive side of that same window from two directions at once:
`POST /api/fleet/queue/peek` is a wholly new route beside the unchanged `GET`, so a runner that predates the route
simply never calls it and keeps reading the unfiltered order; and a registration's `capabilities` field defaults empty
like every other optional field on that model, so a runner that predates the field still parses and registers. An empty
snapshot matches no chunk, though, so a runner that predates capability reporting registers but is never matched and has
every claim refused; [`docs/upgrade.md`](./upgrade.md) owns that boundary.

A registration's `subscriptions` field is additive the same way: it defaults to `None`, so a runner that has never heard
of a declared roster simply omits it, and the hub falls back to its pre-existing age-gated membership rule for that
runner rather than rejecting the registration or defaulting the roster to empty.

## What a tag publishes

Every image below is `ghcr.io/paul-gross/blizzard-hub`.

| Cut                          | Tags published                                                                                                                |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| A stable release, `vX.Y.Z`   | the exact version, its `X.Y` minor line, and `latest` when no higher stable version exists                                    |
| A prerelease, `vX.Y.Z-rc.N`  | the exact version alone — never `latest`, never the `X.Y` minor line                                                          |
| Every green push to `master` | `edge`, a mutable pointer at the newest proven `master` commit, and `sha-<full-git-sha>`, immutable and pinned to that commit |

`latest` never follows `master`; it moves only to the highest stable version, compared numerically per component, so a
patch on an older line leaves it where it is — for the image and for the GitHub Release's "Latest" mark alike. A
deployment that wants a release pins its exact version, and its digest where it can hold one — never `latest` or an
`X.Y` line, which never carry a candidate. [`scripts/image-tags.sh`](../scripts/image-tags.sh) computes the stable
fan-out.

A tag must sit on its line: its commit has to be an ancestor of `origin/release/X.Y`, or the release workflow fails
before building anything. The one exception is a `vX.0.0-rc.K` candidate on `master` while
[`window.toml`](../src/blizzard/wire/window.toml) sets `opening = "X"`.

`pyproject.toml`'s `[project] version` names the release most recently cut on its branch; the minor counts release
trains. A cut commit carrying `Release-Cut: X.Y.Z` moves it to `X.Y.Z`. Candidate `vX.Y.Z-rc.N` and stable `vX.Y.Z` tags
can point to that same commit: [`scripts/check-version-tag.sh`](../scripts/check-version-tag.sh) strips the candidate
suffix when checking against `pyproject.toml`, while the release wheel stamps the full tag version. On `master`, dev
wheels and images target the *next* minor as `X.(Y+1).0.dev<run>+<sha>`; while the window sets `opening = "X"`, they
target the open major as `X.0.0.dev<run>+<sha>`.

Release notes are assembled from the tag's `changes/<X.Y.Z>/` notes and the Conventional Commits since the previous tag
by [`blizzard-change-notes render`](../changes/README.md#cutting-and-rendering). A line-opening release leads with
**Upgrading to <line>**: promised deprecations, their retiring removals and upgrade instructions, then unrelated
breaking notes. Run `blizzard-change-notes guide <line>` before cutting to preview the crossing, including pending
removals in `changes/unreleased/`. Before 1.0 the line is a minor such as `0.12`; from 1.0 it is a major such as `1`.
The current removal line is the latest *released* postponement's `removes-in`, or the deprecation's own when none has
shipped. The cut refuses a new deprecation due no later than the cut line, a non-advancing postponement, or an opening
line with an unretired promise, before moving any notes or boundaries. `check` validates the note format and references
independently of those release-readiness decisions. Older tags without `changes/` still render their commit block.

[`docs/ci.md`](./ci.md) owns the release and dev-publish workflow contracts.
