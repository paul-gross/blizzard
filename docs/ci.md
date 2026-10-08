# CI

The policy behind CI — the branch and release model, one repo one wheel, the four test tiers — is owned by
blizzard-context's
[`verification/blizzard.md`](https://github.com/paul-gross/blizzard-context/blob/master/verification/blizzard.md); this
file is the in-repo operator reference for running it.

## The merge gate

[`.github/workflows/gate.yml`](../.github/workflows/gate.yml) is the reusable (`workflow_call`) merge gate, called by
every trigger workflow: ruff format+check, pyright, the `blizzard:structural-gate` ast-grep scan
(`contracts/ast-grep/`), pytest (unit + component), OpenAPI spec drift, hub↔runner wire compatibility against the PR's
merge-base (`gate / hub↔runner wire compatibility`, PR-only — see below), the `web/` frontend checks (eslint, vitest,
structural gate, generated-client drift), and the Vale prose lint (`gate / process-reference and change-history lint`,
`styles/Blizzard/ProcessReference.yml`, `styles/Blizzard/ChangeHistory.yml` and `styles/Blizzard/VariantHolder.yml`
against `.vale.ini`). The
`mise run process-ref-lint` command covers Markdown, configured Python and `src/` YAML, and web TypeScript/CSS;
generated API clients are excluded and `.html` templates are outside Vale's configured extensions. The change-history
rule flags the change-history phrases its own token list names, which is the one statement of what it catches. Every
gate check is seams-mocked and token-free, needing no real forge, no tokens, and no network beyond package installs.

The pytest tier runs as four parallel shard jobs, and the service tier below as three.
`BLIZZARD_TEST_SHARD=<index>/<count>` (`tests/conftest.py`) keeps one disjoint slice of the collected suite, chosen by a
stable hash of each test's nodeid, so the shards together run every test exactly once. A `*-result` job reports each
sharded tier under its single check name — `gate / pytest (unit + component)`,
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
uv run blizzard-wire-compat --baseline merge-base --against origin/master
cd web && npm ci && npm run lint && npm run test && npm run structural-gate && npm run generate:client && cd .. && git diff --exit-code -- web/
```

`blizzard:wire-compat` (`src/blizzard/tools/wire_compat.py`) fails on a breaking change to the declared hub↔runner wire
surface — `bzh:fleet-wire-additive` owns what counts as breaking. The `gate.yml` job runs only when the triggering event
is `pull_request` (`if: github.event_name == 'pull_request'`), diffing `HEAD` against its merge-base with
`origin/master` so a break is attributed to the PR that makes it. `mise run wire-compat` reproduces it locally against
the current branch's own merge-base.

## The upper tiers

[`.github/workflows/upper-tiers.yml`](../.github/workflows/upper-tiers.yml) is reusable (`workflow_call`) and runs the
service tier (`blizzard:service-test`) and the kill-9 crash sweep's bounded CI profile (`blizzard:crash-sweep`) over a
multi-repo checkout of `blizzard` + `blizzard-mock` + `blizzard-workspace`; `pr.yml` and `push.yml` both call it.

Locally the upper tiers need the sibling `blizzard-mock` worktree provisioned (`winter provision <env>`); their exact
local equivalents are `mise run service-test` (`BLIZZARD_SERVICE=1 uv run pytest -n auto tests/service/`) and
`mise run crash-sweep-ci`
(`BLIZZARD_CRASH_SWEEP=1 BLIZZARD_CRASH_SWEEP_CI=1 uv run pytest -n auto -m crash_sweep tests/crash/`).

The e2e tier runs only locally and in the tag `release` workflow — it is never a `pr`/`push` gate job.

## Trigger workflows

[`.github/workflows/pr.yml`](../.github/workflows/pr.yml) (PR to `master`) runs the gate plus the service tier and
CI-profile crash sweep as real gate jobs.

[`.github/workflows/push.yml`](../.github/workflows/push.yml) (push to `master`) runs the gate and upper tiers, then
uploads a dev-build wheel versioned `0.<milestone>.0.dev<run>+<9-char-sha>` as a workflow artifact and publishes
`ghcr.io/paul-gross/blizzard-hub` under two dev tags, multi-arch like the release image: `edge`, mutable — the newest
proven `master` image — and `sha-<full-git-sha>`, immutable. `edge` is the dogfooding pointer; `latest` never follows
`master` and moves only on a stable release cut — [`docs/versioning.md`](./versioning.md) owns the channel semantics.

The dev-build wheel is downloaded from its run with:

```bash
gh run download --repo paul-gross/blizzard <run-id>
```

The `dev-image` job needs `[gate, upper-tiers, dev-build, wire-compat-deployed]` — the tiers so the channel advances
only on proven commits, `dev-build` solely to reuse its computed dev version for the image's
`org.opencontainers.image.version` annotation rather than recomputing it, and `wire-compat-deployed` so `edge` never
advances past an unacknowledged hub↔runner wire break. Unlike the release fan-out, the dev tags and OCI annotations
are inlined in the `dev-image` job — no branching logic to unit-test — and `tests/test_push_workflow.py` pins the job.

`wire-compat-deployed` diffs every commit since the newest successful `push.yml` run — the first `success` among
`gh run list --workflow push.yml --branch master`'s newest runs, since that run's commit is what `edge` currently runs;
never `--status success` alongside `--branch`, which GitHub answers from a stale index — against `HEAD`, one first-parent step at a time, failing on any step whose break is not acknowledged
by a `!`-marked Conventional Commit subject on the commits that land it (`bzh:fleet-wire-additive`). It needs job-level
`permissions: actions: read` to call `gh run list` with the workflow's own `GITHUB_TOKEN`; the job fails outright if no
successful run is found. Its local equivalent is `uv run blizzard-wire-compat --baseline deployed`, run with the
operator's own `gh` auth.

It checks the net diff from the resolved baseline to `HEAD` first, and skips the per-step walk entirely when that net
diff is additive — the per-step walk exists only to attribute a *surviving* break to the landing that must acknowledge
it. This is the recovery path if an unacknowledged break ever lands on `master` outright (bypassing or predating the PR
gate): since that landing is already pushed, its subject can't be marked `!` after the fact, and rewriting pushed
`master` history is not an option. Push a following commit that reverts the break instead — once the net diff back to
the last successful baseline is clean again, `wire-compat-deployed` passes and `edge` resumes advancing, with no
history rewrite required.

[`.github/workflows/release.yml`](../.github/workflows/release.yml) (tag `v*`) runs the full suite — gate, service tier,
the **full** crash sweep, and e2e — then builds the wheel with the embedded frontend, pushes a multi-arch
(`linux/amd64` + `linux/arm64`) hub image to GHCR, and publishes a GitHub Release with the wheel attached. The Release
is published with the workflow's built-in `GITHUB_TOKEN`; there is no external package-index publish.

The release tag fan-out (`vX.Y.Z` → exact + minor + `latest`; `vX.Y.Z-rc.*` → exact only) lives in
[`scripts/image-tags.sh`](../scripts/image-tags.sh), unit-tested by `tests/test_image_tags.py`, never inline in workflow
YAML.

The `release` job's job-level `permissions:` block declares both `contents: write` (`gh release create`) and
`packages: write` (the GHCR push) — job-level blocks replace the workflow-level one — and the image push runs before
`gh release create`, so a failed image build cannot leave a Release advertising a missing image;
`tests/test_release_workflow.py` pins both.

The `ghcr.io/paul-gross/blizzard-hub` package must be **public** — package visibility is a GHCR repository setting no
workflow can assert, and every unauthenticated `docker pull` the operator docs prescribe depends on it. It is verified
with an anonymous pull:

```bash
docker pull ghcr.io/paul-gross/blizzard-hub:latest
```

`mise run image-smoke` builds and boots the image locally but cannot prove the multi-arch GHCR push — only a real tag
cut's `release` run proves that.

## The wheel build

[`scripts/build-wheel.sh`](../scripts/build-wheel.sh) (`mise run build`) is the single build entrypoint for agents,
humans, and the release workflow alike. It builds both Angular apps into the wheel-embed assets dir
`src/blizzard/static/{hub,runner}`, builds the wheel (`uv build --wheel`) embedding those assets plus both Alembic
migration trees, verifies the wheel actually contains them, then proves a Node-free install by running
`blizzard --version` from a clean node-free virtualenv.

Setting `BLIZZARD_VERSION=<v>` overrides the wheel version — the dev-build and release jobs set it — and the original is
restored after the build.
