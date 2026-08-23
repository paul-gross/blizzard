# Versioning

Blizzard follows [Semantic Versioning](https://semver.org/) as `MAJOR.MINOR.PATCH`, with `MAJOR` pinned at `0` until the
project reaches 1.0 — so under semver's own pre-1.0 carve-out a `MINOR` bump may carry a breaking change.

## What counts as breaking

| Surface         | A release breaks it when it…                                                                                                                                    |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| HTTP API        | removes a route, adds a required field to a request, or removes a response field or changes its meaning                                                         |
| hub↔runner wire | is not wire-compatible with the previous minor — an `/api/fleet/...` route, or a field the runner's `IHubClient` (`src/blizzard/runner/loop/hub.py`) depends on |
| Configuration   | renames or removes a `blizzard-hub.toml` or `blizzard-runner.toml` key, or moves or removes a durable path under the runtime root                               |
| Store schema    | ships a revision that cannot be walked back — breaking regardless of what else the release changed                                                              |

[`docs/backup.md`](./backup.md) owns the current durable layout. Every schema revision blizzard has ever shipped keeps a
working `downgrade()`, held mechanically for every revision in the tree by
`tests/test_store_migrations.py::test_migrate_up_and_down`.

Adding an optional config key, a new route, a new event type, or a migration that walks back cleanly is not breaking.

Mark a breaking commit with a `!` before the colon of its Conventional Commit subject: `feat!: ...`,
`feat(scope)!: ...`.

## The hub↔runner skew window

A runner may lag its hub by one minor version, and a hub never requires a runner newer than itself: hub `0.5.x` works
with runners at `0.4.x` or `0.5.x`, but not `0.3.x`.

That window is policy, not enforcement — nothing checks it at runtime, so there is no version negotiation and no
minimum-runner rejection to catch a runner that has fallen outside it.

Most string-valued wire fields stay open strings, so a value the receiver does not recognize round-trips instead of
failing. `TurnSegmentView.kind` (`src/blizzard/wire/transcript_segment.py`) is the exception: it is typed as a closed
`TurnKind` literal because a transcript viewer branches its rendering on it turn by turn. Closing it costs on both
directions — a runner shipping a kind an older hub does not know 422s the whole ingest batch rather than storing it
opaquely, and a stored segment carrying an out-of-vocabulary kind raises on read instead of round-tripping. Adding a
value to the `TurnKind` vocabulary is therefore breaking against the skew window, not additive.

## What a tag publishes

Every image below is `ghcr.io/paul-gross/blizzard-hub`.

| Cut                          | Tags published                                                                                                                |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| A stable release, `vX.Y.Z`   | the exact version, its `X.Y` minor line, and `latest`                                                                         |
| A prerelease, `vX.Y.Z-rc.N`  | the exact version alone — never `latest`, never the `X.Y` minor line                                                          |
| Every green push to `master` | `edge`, a mutable pointer at the newest proven `master` commit, and `sha-<full-git-sha>`, immutable and pinned to that commit |

`latest` never follows `master`; it moves only on a stable release tag.
[`scripts/image-tags.sh`](../scripts/image-tags.sh) computes the stable fan-out.

`pyproject.toml`'s `[project] version` must equal the tag being cut — `v1.2.3` pairs with `version = "1.2.3"` — and
[`scripts/check-version-tag.sh`](../scripts/check-version-tag.sh) asserts it before the release builds anything, so a
forgotten bump fails the release instead of shipping a wheel that misreports its own version.

Release notes are generated from the Conventional Commits since the previous tag by
[`scripts/release-notes.sh`](../scripts/release-notes.sh). A `!`-marked commit surfaces at the top of the notes under
**Breaking changes**. An **Upgrade notes** section is always emitted — directly beneath that section when the release
has one, at the very top when it does not — as a placeholder when the release asks nothing of the operator, and
hand-written prose whenever it asks for something.

[`docs/ci.md`](./ci.md) owns the release and dev-publish workflow contracts.
