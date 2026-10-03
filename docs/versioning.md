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
| Trace contract  | renames or removes a span name, event name or attribute, changes an attribute's type or meaning, or changes how trace and span ids are derived                  |

[`docs/backup.md`](./backup.md) owns the current durable layout. Every schema revision blizzard has ever shipped keeps a
working `downgrade()`, held mechanically for every revision in the tree by
`tests/test_store_migrations.py::test_migrate_up_and_down`.

Adding an optional config key, a new route, a new event type, a new trace attribute or span, or a migration that walks
back cleanly is not breaking.

Mark a breaking commit with a `!` before the colon of its Conventional Commit subject: `feat!: ...`,
`feat(scope)!: ...`.

## The trace contract

[`contracts/traces/`](../contracts/traces/README.md) pins the shape of a chunk's trace — the chunk span, its step roots
and the runner leases under them: `dictionary.json` is the authored contract, and `golden/` is the spans the hub and a
runner assemble for seeded scenarios. `blizzard:trace-contract` fails when the assembled spans drift from either, so a
shape change is always a deliberate edit to the dictionary.

A rename is not a single release: the release that introduces the new name emits both, for at least one minor, with the
old name marked deprecated in `dictionary.json`. Removing the old name afterwards is the breaking change. Every breaking
trace change raises `blizzard.trace.schema_version` and the instrumentation scope version together, so a backend can
tell shapes apart without reading the release notes. [`docs/deployment/tracing.md`](./deployment/tracing.md) describes
the shape for operators. A raised schema version comes with an upgrade note; schema 2's is
[Upgrading from trace schema 1](./deployment/tracing.md#upgrading-from-trace-schema-1).

## The hub↔runner skew window

A runner may lag its hub by one minor version, and a hub never requires a runner newer than itself: hub `0.5.x` works
with runners at `0.4.x` or `0.5.x`, but not `0.3.x`.

There is still no version negotiation and no minimum-runner rejection to catch a runner that has fallen outside the
window at runtime. What is checked is the per-commit unit the window actually depends on: `blizzard:wire-compat`
(`bzh:fleet-wire-additive`) diffs the declared hub↔runner surface — every `/api/fleet/...` route plus the auth
federation routes the runner calls — between consecutive commits, and fails on a breaking change unless the landing
commit's subject carries a Conventional Commits `!` marker, in which case it reports the break rather than failing. A
merge-base-mode run gates every pull request against its own merge-base; a deployed-mode run gates every push to
`master` against the last commit `edge` was published from, so a hand-redeployed runner from any earlier commit keeps
working until the next acknowledged break. [`docs/ci.md`](./ci.md) owns where each mode runs.

One user-approved exception has been taken against it: the release that removes `RunnerView.external_subscription_usage`
requires every runner to report slug-carrying per-subscription usage **before** the hub is deployed, so across that one
boundary a previous-minor runner is not supported. The hub rejects a slug-less usage sample rather than defaulting it,
and [`docs/upgrade.md`](./upgrade.md) owns the operator-facing ordering and recovery detail. The exception is scoped to
that boundary and does not widen the policy.

Most string-valued wire fields stay open strings, so a value the receiver does not recognize round-trips instead of
failing. `TurnSegmentView.kind` (`src/blizzard/wire/transcript_segment.py`) is the exception: it is typed as a closed
`TurnKind` literal because a transcript viewer branches its rendering on it turn by turn. Closing it costs on both
directions — a runner shipping a kind an older hub does not know 422s the whole ingest batch rather than storing it
opaquely, and a stored segment carrying an out-of-vocabulary kind raises on read instead of round-tripping. Adding a
value to the `TurnKind` vocabulary is therefore breaking against the skew window, not additive.

The capability-matched fleet peek sits on the additive side of that same window from two directions at once:
`POST /api/fleet/queue/peek` is a wholly new route beside the unchanged `GET`, so a previous-minor runner simply never
calls it and keeps reading the unfiltered order; and a registration's `capabilities` field defaults empty like every
other optional field on that model, so a previous-minor runner parses and registers exactly as it always has.

A registration's `subscriptions` field is additive the same way: it defaults to `None`, so a previous-minor runner that
has never heard of a declared roster simply omits it, and the hub falls back to its pre-existing age-gated membership
rule for that runner rather than rejecting the registration or defaulting the roster to empty.

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
