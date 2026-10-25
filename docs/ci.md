# CI

The policy behind CI — the branch and release model, one repo one wheel, the five test tiers — is owned by
blizzard-context's
[`verification/blizzard.md`](https://github.com/paul-gross/blizzard-context/blob/master/verification/blizzard.md); this
file is the in-repo operator reference for running it.

## The merge gate

[`.github/workflows/gate.yml`](../.github/workflows/gate.yml) is the reusable (`workflow_call`) merge gate, called by
every trigger workflow: ruff format+check, pyright, the `blizzard:structural-gate` ast-grep scan
(`contracts/ast-grep/`), the `dprint check` Markdown format (`gate / dprint`, `blizzard:markdown-format`), pytest
(unit + component), OpenAPI spec drift, contract document drift (`gate / contract drift`), compatibility against the
supported releases (`gate / compatibility against supported releases`, every trigger but a tag — see below), the change
notes under `changes/` (`gate / change notes`, every trigger — see below), flag lifecycle (`gate / flag lifecycle`,
PR-only), the Playwright package's static checks (`gate / playwright static`, `mise run playwright-check`: `tsc`, eslint
with the bans a service spec keeps, and a regenerate-and-diff of the committed wire types; no daemon starts), the `web/`
frontend checks (eslint, vitest, structural gate, bundle composition, story render, generated-client drift), and the
Vale prose lint (`gate / process-reference and change-history lint`, `styles/Blizzard/ProcessReference.yml`,
`styles/Blizzard/ChangeHistory.yml`, `styles/Blizzard/VariantHolder.yml` and `styles/Blizzard/ShortFormPointer.yml`
against `.vale.ini`). The `mise run process-ref-lint` command covers Markdown, configured Python and `src/` YAML, and
web TypeScript/CSS; generated API clients are excluded and `.html` templates are outside Vale's configured extensions.
The change-history rule flags the change-history phrases its own token list names, which is the one statement of what it
catches. Every gate check is seams-mocked and token-free, needing no real forge, no tokens, and no network beyond
package installs. `gate / playwright static` is a required check of its own; an operator adds it to branch protection
once the job exists on `master`.

The pytest tier runs as four parallel shard jobs, and the service tier below as three.
`BLIZZARD_TEST_SHARD=<index>/<count>` (`tests/suite_plugin.py`) keeps one disjoint slice of the collected suite, chosen
by a stable hash of each test's nodeid, so the shards together run every test exactly once. A `*-result` job reports
each sharded tier under its single check name — `gate / pytest (unit + component)`,
`upper-tiers / service tier (blizzard:service-test)` — so a required-check list names the tier, not its shard count.
Unset, the variable keeps the whole suite; the local commands below run unsharded.

`mise run gate` ([`scripts/ci-gate.sh`](../scripts/ci-gate.sh)) reproduces the whole merge gate in one command before
pushing. The gate's exact individual commands:

```bash
uv sync
uv run ruff format --check .
uv run ruff check .
uv run pyright
vale --output=line .
uv run ast-grep scan --error=unused-suppression .
uv run pytest -n auto
uv run blizzard-export-openapi --out-dir openapi && git diff --exit-code -- openapi/
uv run blizzard-export-contracts --out-dir contracts && git diff --exit-code -- contracts/ && test -z "$(git ls-files --others --exclude-standard -- contracts/)"
uv run blizzard-compat --commits origin/master..HEAD
uv run blizzard-change-notes check
uv run blizzard-flag-lifecycle pr
cd web && npm ci && npm run lint && npm run test && npm run structural-gate && npm run bundle-check && npm run story-render && npm run generate:client && cd .. && git diff --exit-code -- web/
```

`blizzard:compat` (`src/blizzard/tools/compat/`) holds `HEAD`'s committed surface documents to the supported releases
`docs/versioning.md` defines — `bzh:released-surface-additive` owns what counts as breaking. The `gate.yml` job runs on
every trigger but a tag, with full history, over the event's own range: a pull request's base and head, a push's
`before` and `sha`, and `--line` when the target is a `release/*` branch. A finding the range's base already carries is
reported as inherited and does not fail; a new one fails unless a `!`-marked commit in the range acknowledges it and the
release's baseline marked it deprecated or, with `opening` set in the window, an added breaking change note declares it,
or unless an entry in `contracts/compat/allowances.toml` admits it — that file's README owns when one does.
`mise run compat` reproduces it locally against `origin/master`; `--json` prints every finding with its verdict.

`blizzard-change-notes check` (`src/blizzard/tools/change_notes/`) fails on a file under `changes/` that breaks the
format [`changes/README.md`](../changes/README.md) documents, naming the file and the rule for every failure. The
`gate.yml` job runs on every trigger, and `mise run change-notes` reproduces it locally.

`gate / flag lifecycle` runs only on pull requests with a full tag history. It parses HEAD and the registries at valid
release tags, reports each flag's release-derived lifetime and rule-labelled findings, and fails on lifecycle
violations. It runs in `cut` mode on a release-cut pull request — detected by `scripts/release-cut-in-range.sh` over the
pull request's base and head, the detector the upgrade rehearsal uses — and in `pr` mode otherwise, passing `--base` the
base SHA. Three rules read the supported releases from `blizzard-compat releases --json`, failing the check by name when
that call fails: `tombstone-removable` (a `NOTE`, never failing) names a `[retired]` entry no supported release holds
live; `tombstone-removed` fails when a name a supported release holds live is in neither HEAD's live nor retired flags;
and `flip-without-note` (`pr` mode only) fails a flag turned from `off` to `on` against `--base` when no
`changes/unreleased/` note at HEAD names it. `mise run flag-lifecycle` runs `pr` mode with `--base origin/master`; pass
`cut` or `tag` to apply the stricter due-flag policy when checking a release cut or tag.

## The upper tiers

[`.github/workflows/upper-tiers.yml`](../.github/workflows/upper-tiers.yml) is reusable (`workflow_call`) and runs the
service tier (`blizzard:service-test`) and the kill-9 crash sweep (`blizzard:crash-sweep`) over a two-repo checkout of
`blizzard` + `blizzard-mock`; `pr.yml`, `push.yml` and `release.yml` all call it. Two inputs choose the shape. With
`full_suite: true` it also runs the e2e acceptance loop (`blizzard:e2e`), the acceptance journey (`blizzard:journey`),
and the hub image and reference compose smokes (`blizzard:image-smoke`, `blizzard:compose-smoke`) as parallel jobs;
`crash_sweep_ci: "1"` runs the sweep's bounded profile and an empty value the full one. `pr.yml` and `release.yml` pass
`full_suite: true` and the full sweep; `push.yml` keeps the service tier and the bounded sweep, since the `edge` channel
is not held behind e2e and an admin's direct push is ungated by design. The two smokes build their own wheel and need
only docker, compose v2, node and uv, not the mock. A leading `mock-ref` job resolves the mock once, and every tier
checks out that commit. The resolution — which mock tag or branch a tag run, a release-line pull request, and a `master`
run each prove against, and when it fails — is owned by
[`.github/actions/resolve-mock-ref/`](../.github/actions/resolve-mock-ref/action.yml) and its script, which
`release.yml`'s tier jobs resolve through too. The tiers are winter-free: their runners use the built-in `basic`
provider over a fixture of bare `file://` origins, minted with no winter source. Every checkout in a job that runs tests
sets `persist-credentials: false`; `tests/test_winter_free_ci.py` pins both.

Store-migration tests live beside their revisions, in `src/blizzard/{hub,runner}/store/migrations/tests/`. Those whose
revision is more than 30 days old report as skips in `uv run pytest -n auto`; set
`BLIZZARD_RUN_RETIRED_MIGRATION_TESTS=1` to run them (`bzh:colocated-migration-tests` in blizzard-context's tier rules).

Locally the mock-backed tiers need the sibling `blizzard-mock` worktree provisioned (`winter provision <env>`). One task
reproduces the unconditional pull-request tiers: `mise run gate-full` (`blizzard:gate-full`) runs the merge gate, then
`service-test`, the full `crash-sweep`, `e2e`, `journey`, `image-smoke` and `compose-smoke` serially, since the smokes
bind fixed ports. A red tier does not stop the later ones; it ends with a per-tier verdict and exits non-zero on any
red. Run the conditional checks separately when applicable: `mise run store-baseline` for store revisions and
`mise run rehearsal` for a release cut (both need `gh` and Postgres).

Individual commands include `mise run service-test` (`BLIZZARD_SERVICE=1 uv run pytest -n auto tests/service/`) and
`mise run crash-sweep-ci` for the bounded profile `push.yml` runs. The `e2e`, `journey`, `image-smoke` and
`compose-smoke` tasks can also run alone.

**Wall-clock time.** The jobs run in parallel, so a pull request takes as long as its slowest job: e2e. On the `v0.2.0`
tag run the e2e job took 1453s, the full crash sweep 837s, the service tier 613s, and the merge gate's pytest shards
about 1000s. Measured locally, the journey takes about 45s and each smoke about 80s on a warm docker cache, so none adds
to the critical path. A pull request therefore finishes in roughly 25 minutes plus runner queueing, against about 17
without e2e. A release cut adds the upgrade rehearsal after its wheel build.

The winter binding's own tests are the `winter-binding` tier (`mise run winter-binding`, gated on
`BLIZZARD_WINTER_BINDING=1`): local-only, run against a winter source found via `BLIZZARD_MOCK_WINTER_SOURCE` or the
enclosing winter workspace, and never in CI.

## The upgrade rehearsal

[`.github/workflows/rehearsal.yml`](../.github/workflows/rehearsal.yml) is reusable (`workflow_call`) and runs the
upgrade rehearsal (`blizzard:rehearsal`): each supported release's own published wheel seeds a hub and a runner host,
the hub and then the runner are upgraded to a candidate wheel the caller names, and both stores are walked down to the
release and forward again. `push.yml` calls it on every `master` push with the dev-build wheel, and `release.yml` on
every `-rc.` tag with the wheel it is about to release; `pr.yml` calls it on a release cut's pull request, with a wheel
built from the pull request's head. A leading `releases` job lists the supported set with
`blizzard-compat releases --json`, leaving a tag run's own tag out with `--tag`, and each release runs as two legs,
SQLite and Postgres, against a `postgres:16` service. With no supported release, one leg reports that there is nothing
to rehearse and passes. Each leg checks the mock out at the release's own tag where the mock carries one, else at
`master`, through `resolve-mock-ref`'s `baseline` input. A failed leg uploads its daemons' logs as
`rehearsal-logs-<release>-<backend>`.

A release cut is a pull request with a commit carrying a `Release-Cut:` trailer, which
[`scripts/release-cut-in-range.sh`](../scripts/release-cut-in-range.sh) reads with the same
[`scripts/trailers.sh`](../scripts/trailers.sh) helper `line-provenance` uses. On a cut, `cut-wheel` builds the head's
wheel and requirements unstamped, so they carry the cut's `pyproject.toml` version, and `rehearsal` upgrades each
supported release to it. The always-running `rehearsal-result` job (`upgrade rehearsal (release cut)`) is the required
check: it passes when the rehearsal passed, or when the pull request is not a cut and the rehearsal was skipped.

Locally the same tier is `mise run rehearsal`, which builds a candidate unless `BLIZZARD_REHEARSAL_CANDIDATE` names one;
set `BLIZZARD_REHEARSAL_PG_URL` to a Postgres server to run the Postgres backend too.

## The store baseline

[`.github/workflows/store-baseline.yml`](../.github/workflows/store-baseline.yml) is reusable (`workflow_call`) and runs
the store baseline proof (`blizzard:store-baseline`): once a fold has replaced a store's migration history with one
baseline revision, the proof builds the store that baseline builds alone and the store the floor release's own published
wheel builds from its history, and fails on any difference in schema or rows. It runs for the hub on SQLite and Postgres
and for the runner on SQLite and Postgres, against a `postgres:16` service. The runner's history cannot reach head on
Postgres from an empty store, so that leg holds the baseline's Postgres store to the floor's committed
`contracts/store/runner.json` and its rows to the history's SQLite rows. Until a tree declares a `BASELINE_FLOOR` the
proof skips and says so, which is every run before the first fold.

`pr.yml` calls it when the pull request changes either store's `versions/` directory; a `versions-changed` job makes
that check, so no other `pr.yml` job is gated on the paths. `release.yml` calls it on a candidate (`-rc.`) or patch tag
and skips it on a stable promotion `vX.Y.0`, whose tree is its last candidate's; `build` waits on it and runs past it
when skipped. On a pull request the always-running `store-baseline-result` job
(`store baseline
(blizzard:store-baseline)`) is the required branch-protection check: it passes when the proof passed,
or when no revision changed and the proof was skipped.

Locally the same tier is `mise run store-baseline`; set `BLIZZARD_STORE_BASELINE_PG_URL` to a Postgres server to run the
Postgres legs, and give `gh` a token (`GH_TOKEN`, plus `GH_REPO` outside a GitHub checkout) to download the floor's
wheel.

## Trigger workflows

[`.github/workflows/pr.yml`](../.github/workflows/pr.yml) (PR to `master` or `release/**`) runs the gate plus the whole
suite as parallel real gate jobs: the service tier, the full crash sweep, e2e, the journey and the two smokes, and on a
release cut the upgrade rehearsal. A pull request into a release branch also runs `line-provenance`, which has
[`scripts/check-line-provenance.sh`](../scripts/check-line-provenance.sh) fail any commit the pull request adds that is
not a cherry-pick of a commit on `master`, a `Line-only:` commit, or a `Release-Cut:` commit. A push to a release branch
runs nothing: `push.yml` is `master`'s alone. A pull request that changes a store's `versions/` directory also runs the
store baseline proof.

[`.github/workflows/push.yml`](../.github/workflows/push.yml) (push to `master`) runs the gate, the service tier and the
bounded crash sweep, then uploads a dev-build wheel versioned `X.(Y+1).0.dev<run>+<9-char-sha>` from the most recently
cut `X.Y.Z` as a workflow artifact and publishes `ghcr.io/paul-gross/blizzard-hub` under two dev tags, multi-arch like
the release image: `edge`, mutable — the newest proven `master` image — and `sha-<full-git-sha>`, immutable. `edge` is
the dogfooding pointer; `latest` never follows `master` and moves only on a stable release cut —
[`docs/versioning.md`](./versioning.md) owns the channel semantics.

The `proven-against` job, after the gate and upper tiers, uploads a `proven-against` artifact holding one JSON file that
names the `blizzard` commit and the `blizzard-mock` commit the upper tiers ran against. A cut reads the mock commit its
candidate was proven with from the candidate's own run:

```bash
gh run download <run-id> --repo paul-gross/blizzard --name proven-against
```

The dev-build wheel is downloaded from its run with:

```bash
gh run download --repo paul-gross/blizzard <run-id>
```

The `dev-image` job needs `[gate, upper-tiers, dev-build, rehearsal]` — the tiers and the upgrade rehearsal so the
channel advances only on proven commits, and `dev-build` solely to reuse its computed dev version for the image's
`org.opencontainers.image.version` annotation rather than recomputing it. Unlike the release fan-out, the dev tags and
OCI annotations are inlined in the `dev-image` job — no branching logic to unit-test — and `tests/test_push_workflow.py`
pins the job.

[`.github/workflows/release.yml`](../.github/workflows/release.yml) (tag `v*`) opens with `proof`, which asks
[`scripts/borrowed-proof.sh`](../scripts/borrowed-proof.sh), unit-tested by `tests/test_borrowed_proof.py`, whether the
tag's commit already carries a successful `release` run of a `vX.Y.Z-rc.K` tag of the same version. If it does, the
proof is *borrowed*: `gate`, `upper-tiers`, and `isolation-cross-check` are skipped and the run goes on to build. A
promotion `vX.Y.0` with no such run is refused, failing the run before anything is built; a patch `vX.Y.P` has no
candidate to borrow from, so it runs the full suite; a candidate never borrows. `proof` is the only job holding
`actions: read`, with `contents: read` and nothing else. `wheel` checks out full history so `check-version-tag.sh` can
hold the commit to `origin/release/X.Y`. Unless the proof is borrowed, the run executes the full suite — gate, the
`compat` backstop, and `upper-tiers` called with `full_suite: true` and the **full** crash sweep (service tier, crash
sweep, e2e, journey and the two smokes) — builds the wheel once in `wheel`, rehearses the upgrade to it on an `-rc.` tag
alone, proves the store baseline on a candidate or patch tag, then runs three jobs. `flag-lifecycle`
(`release / flag lifecycle`) runs `blizzard-flag-lifecycle tag` with full history before any build, on every tag — a
stable tag's borrowed-proof run included, which skips `gate` — and `build` waits on it. `compat`
(`release / compatibility against supported releases`) runs `blizzard-compat --tag <version>` before any build: it
judges the tag's tree against each supported release over that release's own `R..HEAD` range, fails a break the version
does not admit, and uploads `compat-breaks.json`, which `build` fetches to the repository root. `wheel` checks the
version against the tag and builds the wheel with the embedded frontend and its hashed requirements; `rehearsal`
upgrades each supported release to that wheel and is skipped on a stable tag. `build`, which waits on both and runs only
when nothing it needs failed, takes that same wheel, pushes a multi-arch (`linux/amd64` + `linux/arm64`) hub image to
GHCR, signs it keylessly by its manifest-list digest (`cosign sign --yes --recursive`), attests its build provenance,
writes three SPDX 2.3 JSON bills of materials with Syft — one for each of the hub image's `linux/amd64` and
`linux/arm64` platform images, one for the wheel's runtime — and attests each with `attest-sbom` against the digest or
wheel it describes, assembles the release's attached files with
[`scripts/release-inventory.sh`](../scripts/release-inventory.sh) — the wheel, both hashed requirements files with their
project lines, the image file naming the digest, `compat-breaks.json` (attached by every release built from a commit at
or after the one that added it, so not `v0.2.0` or a patch on `release/0.2`), and `SHA256SUMS` over all of them —
attests the provenance of every attached file, and uploads them as one workflow artifact. `publish` then downloads that
artifact and makes one `gh release create` call with every file attached; there is no external package-index publish.
The release body is the file `build` renders from the tag's tree with `blizzard-change-notes render`, and `publish`
passes it to that call as `--notes-file`; nothing edits the release afterwards, since it is published immutable.
`verify` runs last, once the release page exists, holding only `contents: read` and a copy of
[`scripts/verify-release.sh`](../scripts/verify-release.sh) taken from the tag's tree: it proves the image's signature
and attestations, the attached files, and an install from the release page, so a red `verify` is a red run on a release
that already exists. Anyone can run the same check as `scripts/verify-release.sh <tag>` with `gh` (with its
`attestation` command), `cosign`, and `uv`, and no checkout; it refuses a release cut before `v0.2.0-rc.2`, the first
one it can verify.

The release tag fan-out (`vX.Y.Z` → exact + minor, plus `latest` only when no higher stable tag exists; `vX.Y.Z-rc.*` →
exact only) lives in [`scripts/image-tags.sh`](../scripts/image-tags.sh), unit-tested by `tests/test_image_tags.py` —
`build` hands it the repository's tags and `publish` creates the GitHub Release with `--latest=false` unless `latest`
moved — and the attached set's assembly in `scripts/release-inventory.sh`, unit-tested by
`tests/test_release_inventory.py`, and its post-publish proof in `scripts/verify-release.sh`, unit-tested by
`tests/test_verify_release.py` — never inline in workflow YAML.

The workflow-level `permissions:` is `contents: read`, so the release workflow's test jobs hold no write scope.
Job-level blocks replace the workflow-level one: `build` declares `contents: read`, `packages: write` (the GHCR push),
`id-token: write` (the OIDC identity), and `attestations: write`; `publish` declares only `contents: write`
(`gh release create`); `verify` declares only `contents: read`. The image push runs in `build`, before `publish`, so a
failed image build cannot leave a Release advertising a missing image; `tests/test_release_workflow.py` pins each job's
scopes and the read-only workflow level.

Every signature and attestation carries the identity
`https://github.com/paul-gross/blizzard/.github/workflows/release.yml@refs/tags/v<version>`, issued by
`https://token.actions.githubusercontent.com`; no key exists to rotate.

Two repository settings back that identity. Like package visibility, no workflow can assert them; a real cut is their
only proof:

- A **tag ruleset on `v*`** that lets only the maintainers create a release tag and lets nobody update or delete one.
- **Immutable releases**, so a published release's tag and attached files cannot be replaced after the fact.

The `ghcr.io/paul-gross/blizzard-hub` package must be **public** — package visibility is a GHCR repository setting no
workflow can assert, and every unauthenticated `docker pull` the operator docs prescribe depends on it. It is verified
with an anonymous pull:

```bash
docker pull ghcr.io/paul-gross/blizzard-hub:latest
```

`mise run image-smoke` builds and boots the image, in CI on every pull request and locally, but cannot prove the
multi-arch GHCR push — only a real tag cut's `build` run proves that.

## The wheel build

[`scripts/build-wheel.sh`](../scripts/build-wheel.sh) (`mise run build`) is the single build entrypoint for agents,
humans, and the release workflow alike. It builds both Angular apps into the wheel-embed assets dir
`src/blizzard/static/{hub,runner}`, builds the wheel (`uv build --wheel`) embedding those assets plus both Alembic
migration trees, verifies the wheel actually contains them, then proves a Node-free install by running
`blizzard --version` from a clean node-free virtualenv.

Setting `BLIZZARD_VERSION=<v>` overrides the wheel version — the dev-build and release `build` jobs set it — and the
original is restored after the build.
