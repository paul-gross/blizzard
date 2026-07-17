# CI, build, and release

How `blizzard` code becomes checked, built, and released — the GitHub Actions
workflows, the one build entrypoint, and the exact local commands that equal the
merge gate. The policy behind this (branch/release model, one repo one wheel,
the four test tiers) is owned by the harness
(`blizzard-harness:/verification/blizzard.md`); this file is the in-repo operator
reference for running it.

## Workflows

| File | Trigger | Runs |
|------|---------|------|
| [`.github/workflows/gate.yml`](../.github/workflows/gate.yml) | reusable (`workflow_call`) | The merge gate: ruff format+check, pyright, pytest (unit + component), OpenAPI spec drift, and — once the `web/` workspace lands — eslint, vitest, and generated-client drift. Defined once; every trigger below calls it. |
| [`.github/workflows/upper-tiers.yml`](../.github/workflows/upper-tiers.yml) | reusable (`workflow_call`) | The service tier (`blizzard:service-test`) and the kill-9 crash sweep's bounded CI profile (`blizzard:crash-sweep`), over a multi-repo checkout (`blizzard` + `blizzard-mock` + `blizzard-workspace`). Defined once; `pr.yml` and `push.yml` both call it. |
| [`.github/workflows/pr.yml`](../.github/workflows/pr.yml) | PR to `master` | The gate, plus the service tier and crash sweep (CI profile) as real gate jobs. |
| [`.github/workflows/push.yml`](../.github/workflows/push.yml) | push to `master` | The gate, plus the service tier and crash sweep (CI profile) as real gate jobs, plus a **dev-build wheel** (`0.<milestone>.0.dev<run>`) uploaded as a workflow artifact. |
| [`.github/workflows/release.yml`](../.github/workflows/release.yml) | tag `v*` | The full suite (gate, service tier, the FULL crash sweep, and e2e), a wheel built with the embedded frontend, and a **GitHub Release** with the wheel attached. |

All gate checks are seams-mocked and token-free — they install dependencies and
run, needing no real forge, no tokens, and no network beyond package installs.
The GitHub Release step uses the workflow's built-in `GITHUB_TOKEN`; there is no
external package-index publish.

### Pending pieces, named not hidden

- **Frontend gate steps** (eslint, vitest, generated-client drift) and the
  frontend half of the build activate when the P5 frontend builder lands the
  Angular workspace at `web/` with `npm run lint`, `npm run test`, and
  `npm run generate:client`. Until then each is a clearly-labeled no-op. The
  path and script names are the declared interface; the P5 integrate step
  reconciles them.
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
# frontend (once web/ lands): cd web && npm ci && npm run lint && npm run test && npm run generate:client && git diff --exit-code web/
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
