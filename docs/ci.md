# CI, build, and release

How `blizzard` code becomes checked, built, and released — the GitHub Actions
workflows, the one build entrypoint, and the exact local commands that equal the
merge gate. The policy behind this (branch/release model, one repo one wheel,
the four test tiers) is owned by the harness
(`blizzard-context:/verification/blizzard.md`); this file is the in-repo operator
reference for running it.

## Workflows

| File | Trigger | Runs |
|------|---------|------|
| [`.github/workflows/gate.yml`](../.github/workflows/gate.yml) | reusable (`workflow_call`) | The merge gate: ruff format+check, pyright, pytest (unit + component), OpenAPI spec drift, and — once the `web/` workspace lands — eslint, vitest, and generated-client drift. Defined once; every trigger below calls it. |
| [`.github/workflows/upper-tiers.yml`](../.github/workflows/upper-tiers.yml) | reusable (`workflow_call`) | The service tier (`blizzard:service-test`) and the kill-9 crash sweep's bounded CI profile (`blizzard:crash-sweep`), over a multi-repo checkout (`blizzard` + `blizzard-mock` + `blizzard-workspace`). Defined once; `pr.yml` and `push.yml` both call it. |
| [`.github/workflows/pr.yml`](../.github/workflows/pr.yml) | PR to `master` | The gate, plus the service tier and crash sweep (CI profile) as real gate jobs. |
| [`.github/workflows/push.yml`](../.github/workflows/push.yml) | push to `master` | The gate, plus the service tier and crash sweep (CI profile) as real gate jobs, plus a **dev-build wheel** (`0.<milestone>.0.dev<run>`) uploaded as a workflow artifact, plus a **multi-arch dev hub image** (`edge` + `sha-<full-git-sha>`) pushed to GHCR. |
| [`.github/workflows/release.yml`](../.github/workflows/release.yml) | tag `v*` | The full suite (gate, service tier, the FULL crash sweep, and e2e), a wheel built with the embedded frontend, a **multi-arch hub container image** (`linux/amd64`+`linux/arm64`) pushed to GHCR, and a **GitHub Release** with the wheel attached. |

All gate checks are seams-mocked and token-free — they install dependencies and
run, needing no real forge, no tokens, and no network beyond package installs.
The GitHub Release step uses the workflow's built-in `GITHUB_TOKEN`; there is no
external package-index publish.

### The image publish (tag `release` only)

The `release` job's own `permissions:` block declares both `contents: write` (for
`gh release create`) and `packages: write` (for the GHCR push) — the repo's first
job-level `permissions:` block, since a job-level block replaces the
workflow-level one rather than merging with it
(`tests/test_release_workflow.py` pins both scopes and that the image build-push
step runs before `gh release create`, per decision 5 in the DISTRIB plan: a
failed image build must never leave a published Release advertising an image
that does not exist). The tag fan-out (`vX.Y.Z` → exact + minor + `latest`;
`vX.Y.Z-rc.*` → exact only) is `scripts/image-tags.sh`
(`tests/test_image_tags.py`), not workflow-inline logic.

**Operator step, one-time:** GHCR package visibility (public vs. private) is a
repository setting a workflow cannot assert — after the first tag cut, confirm
`ghcr.io/paul-gross/blizzard-hub`'s package visibility is **public** (repo →
Packages → package settings), then confirm an anonymous pull succeeds:

```bash
docker pull ghcr.io/paul-gross/blizzard-hub:latest
```

**What is not proven by any local method:** whether the GHCR push itself
succeeds — no `docker buildx` plugin is available on the local dev machine, so
`blizzard:image-smoke` builds and boots the image locally but never pushes it
multi-arch. This is a documented gap, the same shape `bzh:release` already
carries for the release cut generally: proven by the next real tag cut
(`blizzard:ci` watching the `release` run, then the anonymous pull above), not
invented around.

### The dev image publish (push to `master`)

Every green `master` commit publishes `ghcr.io/paul-gross/blizzard-hub` with two
mutable-vs-immutable dev tags — `edge` (always the newest proven `master`
image) and `sha-<full-git-sha>` (the exact commit, never reused) — built for
both `linux/amd64` and `linux/arm64` the same way the release image is. This
is a continuous, self-hostable artifact channel: no deployment, webhook, or
installation-specific behavior is triggered by publishing it. It carries no
branching logic (unlike the release fan-out, `scripts/image-tags.sh`), so the
two tags and the OCI annotations are inlined directly in the `dev-image` job
rather than farmed out to a script (`tests/test_push_workflow.py`).

`dev-image` needs `[gate, upper-tiers, dev-build]` — the first two so the
channel only ever advances on a commit that has cleared the merge gate and the
service/crash-sweep tiers, and `dev-build` (not just `gate`) purely so the
image's `org.opencontainers.image.version` annotation can reuse `dev-build`'s
already-computed `0.<milestone>.0.dev<run>` string instead of recomputing it a
second time.

**`latest` never follows `master`** — that tag stays reserved for a stable
release cut (see `docs/versioning.md`'s dev channel section). `edge` is the
mutable pointer for dogfooding; `latest` is the mutable pointer for the public,
stable channel, and the two must not be confused.

### Pending pieces, named not hidden

- **Frontend gate steps** (eslint, vitest, the structural gate, and
  generated-client drift) are live: the `frontend` job runs `npm run lint`,
  `npm run test`, `npm run structural-gate`, and `npm run generate:client`
  against the `web/` Angular workspace on every push.
- **Service tier and crash sweep** are real gate jobs as of P6/P7: `pr.yml` and
  `push.yml` both run them (via `upper-tiers.yml`) at the bounded CI crash-sweep
  profile, and `release.yml` runs them at full strength. Only the **e2e tier**
  remains a local + tag-`release`-only tier — it is not a `pr`/`push` gate job.

## The one build entrypoint

[`scripts/build-wheel.sh`](../scripts/build-wheel.sh) (`mise run build`) is the
single entrypoint an agent, a human, or the release workflow invokes. It:

1. builds both Angular apps and writes their output into the wheel-embed assets
   dir (`src/blizzard/static/{hub,runner}`) — a no-op shipping the committed
   placeholder assets until the `web/` workspace lands;
2. builds the wheel (`uv build --wheel`), embedding those assets plus both
   Alembic migration trees;
3. verifies the wheel actually contains the embedded assets and both migration
   trees; and
4. installs the wheel into a clean, **node-free** virtualenv and runs
   `blizzard --version`, proving the released artifact needs no Node at install
   or runtime.

Set `BLIZZARD_VERSION=<v>` to override the wheel version (the dev-build and
release jobs do this); it is restored after the build.

## Local parity — the exact commands the gate runs

Run the whole gate in one command before pushing:

```bash
mise run gate          # == scripts/ci-gate.sh
```

Or run each check individually — these are exactly what the `gate` workflow runs:

```bash
uv sync                                        # install (bzh:python-toolchain)
uv run ruff format --check .                   # format
uv run ruff check .                            # lint
uv run pyright                                 # typecheck
uv run pytest -n auto                          # unit + component tiers, parallel
uv run blizzard-export-openapi --out-dir openapi && git diff --exit-code openapi/   # spec drift
cd web && npm ci && npm run lint && npm run test && npm run structural-gate && npm run generate:client && git diff --exit-code web/   # frontend
```

The `pr` and `push` workflows also run the service tier and the crash sweep's CI
profile as their own gate jobs (`upper-tiers.yml`, needing the sibling
`blizzard-mock` worktree provisioned — `winter provision <env>`); their exact
local equivalents:

```bash
mise run service-test    # == BLIZZARD_SERVICE=1 uv run pytest tests/service/
mise run crash-sweep-ci  # == BLIZZARD_CRASH_SWEEP=1 BLIZZARD_CRASH_SWEEP_CI=1 uv run pytest -m crash_sweep tests/crash/
```

## Watching runs

```bash
gh run list --repo paul-gross/blizzard                 # recent runs across all workflows
gh run list  --repo paul-gross/blizzard --workflow push.yml   # just the push-to-master runs
gh run watch --repo paul-gross/blizzard <run-id>       # live-tail a run
gh run view  --repo paul-gross/blizzard <run-id> --log-failed  # failed-step logs
gh run download --repo paul-gross/blizzard <run-id>    # fetch the dev-build wheel artifact
```
