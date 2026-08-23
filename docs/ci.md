# CI

The policy behind CI — the branch and release model, one repo one wheel, the four test tiers — is owned by
blizzard-context's
[`verification/blizzard.md`](https://github.com/paul-gross/blizzard-context/blob/master/verification/blizzard.md); this
file is the in-repo operator reference for running it.

## The merge gate

[`.github/workflows/gate.yml`](../.github/workflows/gate.yml) is the reusable (`workflow_call`) merge gate, called by
every trigger workflow: ruff format+check, pyright, pytest (unit + component), OpenAPI spec drift, and the `web/`
frontend checks (eslint, vitest, structural gate, generated-client drift). Every gate check is seams-mocked and
token-free, needing no real forge, no tokens, and no network beyond package installs.

`mise run gate` ([`scripts/ci-gate.sh`](../scripts/ci-gate.sh)) reproduces the whole merge gate in one command before
pushing. The gate's exact individual commands:

```bash
uv sync
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -n auto
uv run blizzard-export-openapi --out-dir openapi && git diff --exit-code -- openapi/
cd web && npm ci && npm run lint && npm run test && npm run structural-gate && npm run generate:client && cd .. && git diff --exit-code -- web/
```

## The upper tiers

[`.github/workflows/upper-tiers.yml`](../.github/workflows/upper-tiers.yml) is reusable (`workflow_call`) and runs the
service tier (`blizzard:service-test`) and the kill-9 crash sweep's bounded CI profile (`blizzard:crash-sweep`) over a
multi-repo checkout of `blizzard` + `blizzard-mock` + `blizzard-workspace`; `pr.yml` and `push.yml` both call it.

Locally the upper tiers need the sibling `blizzard-mock` worktree provisioned (`winter provision <env>`); their exact
local equivalents are `mise run service-test` (`BLIZZARD_SERVICE=1 uv run pytest tests/service/`) and
`mise run crash-sweep-ci`
(`BLIZZARD_CRASH_SWEEP=1 BLIZZARD_CRASH_SWEEP_CI=1 uv run pytest -m crash_sweep tests/crash/`).

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

The `dev-image` job needs `[gate, upper-tiers, dev-build]` — the tiers so the channel advances only on proven commits,
and `dev-build` solely to reuse its computed dev version for the image's `org.opencontainers.image.version` annotation
rather than recomputing it. Unlike the release fan-out, the dev tags and OCI annotations are inlined in the `dev-image`
job — no branching logic to unit-test — and `tests/test_push_workflow.py` pins the job.

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
