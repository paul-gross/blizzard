# Blizzard

The main application — the **hub**, the **runner**, the **CLI**, and the web board for orchestrating autonomous fleets of coding agents.

One repo, one wheel: the single distributable ships both daemons, the CLI, and the compiled frontend as embedded assets — no Node at install or runtime.

Spend is metered and boundable: every attempt's token usage and cost are recorded as facts, surfaced per chunk and fleet-wide on the board and `blizzard hub status`, and optionally capped — a per-chunk spend cap and a runner spend kill-switch (see [`docs/deployment.md`](docs/deployment.md) → "Bounding fleet spend").

## Install

Milestone builds are published as [GitHub Releases](https://github.com/paul-gross/blizzard/releases) with the wheel attached — no package index. Prerelease candidates are tagged `v0.1.0-rc.N`. Grab the wheel and install it into any Python ≥ 3.12 environment (no Node needed at install or runtime):

```bash
gh release download v0.1.0-rc.1 --repo paul-gross/blizzard --pattern '*.whl'
pip install ./blizzard-*.whl        # installs `blizzard`, `blizzard-hub`, `blizzard-runner`
blizzard --version
```

## Quickstart from a release

Install the wheel and bring the hub up — sqlite store, embedded board, no Node:

```bash
pip install https://github.com/paul-gross/blizzard/releases/download/v0.1.0-rc.1/blizzard-0.1.0rc1-py3-none-any.whl
blizzard hub init .          # scaffold config + data dir + migrated sqlite store
blizzard hub host .          # serve the API + embedded mission-control board
```

Then open <http://127.0.0.1:8421/> — the default port from the `blizzard-hub.toml` that `blizzard hub init` scaffolds.

- **sqlite is the default store** — postgres is configuration (the `db_url` knob), not a requirement.
- **The mission-control frontend is embedded in the wheel** — no Node install or runtime.
- The same `init` / `migrate` / `host` verbs exist under `blizzard runner`.

## Layout (screaming architecture — `bzh:screaming-architecture`)

The top-level packages announce what blizzard *is*: two daemons and the client that speaks to them.

| Package | What it is |
|---------|-----------|
| `src/blizzard/hub/` | the `blizzard-hub` daemon — the work orchestrator. `api/` HTTP edge, `domain/` core, `store/` with its **own** Alembic tree. |
| `src/blizzard/runner/` | the `blizzard-runner` daemon — the supervisor. Same `api/` + `domain/` + `store/` shape, an **independent** Alembic tree. |
| `src/blizzard/cli/` | the `blizzard` binary's root command group — verbs namespaced by target (`blizzard hub …`, `blizzard runner …`). |
| `src/blizzard/foundation/` | the shared kernel both daemons compose: the injected clock (`bzh:injected-clock`), structlog wiring, the portable store engine, and the Alembic migration runner + revision-mismatch guard. |
| `src/blizzard/static/` | the wheel-embedded frontend assets seam — CI fills `hub/` and `runner/` with the compiled Angular apps ([static/README.md](./src/blizzard/static/README.md)). |
| `src/blizzard/tools/` | dev/CI tooling — the OpenAPI exporter (`blizzard-export-openapi`). |

## Commands

```bash
uv sync                        # install (bzh:python-toolchain)
uv run ruff check .            # lint
uv run ruff format --check .   # format
uv run pyright                 # typecheck
uv run pytest                  # unit + component tiers (hermetic, token-free)
mise run service-test          # the service tier — a running daemon vs. its mocked counterpart (see below)
mise run e2e                   # the standing e2e smoke suite — six full-stack scenarios (see below)

blizzard hub init ./hub-data   # scaffold config + data dir + migrated DB (idempotent)
blizzard hub migrate           # apply pending store migrations (--down <rev> reverses schema; some revisions are lossy — see the revision's docstring)
blizzard hub host --dir ./hub-data   # serve; bare `blizzard hub` defaults to host

blizzard-export-openapi --out-dir openapi   # dump hub + runner OpenAPI specs
```

The same `init` / `migrate` / `host` verbs exist under `blizzard runner`. A daemon **refuses to start on a store-revision mismatch**, naming the exact `migrate` command (`bzh:manual-migrations`).

## The standing e2e smoke suite (`mise run e2e`)

`mise run e2e` (`BLIZZARD_E2E=1 uv run pytest tests/e2e/`) is the standing end-to-end smoke suite — the acceptance criterion for the whole system. It grew from the P6 acceptance loop to **six** full-stack scenarios over the `build → review → deliver` default shape and its human-loop and operator-surface variants:

1. `test_acceptance_loop` — the happy path: one chunk travels ingest → acquire → mock-scripted commit → review (PASS) → deliver → landed on bare `main`;
2. `test_review_cycle_e2e` — review fails once, the findings + prompt_addendum thread back into build, then it lands on the second pass;
3. `test_escalation_e2e` — two verdict-less exits exhaust the retry budget, the chunk derives `needs_human`, and the surfaced takeover command resumes the parked session;
4. `test_ask_answer_e2e` — a build worker asks and parks `waiting_on_human`; `blizzard hub answer` resumes the dormant session and it lands;
5. `test_gate_decision_e2e` — a human `approve-gate` parks a Decision; `blizzard hub decide` approves and it delivers;
6. `test_board_browser_e2e` — the **browser tier**: a real Chromium (Playwright) drives the served mission-control board — a live status chip that flips over SSE with no reload, the detail drawer's history + artifacts, queue grouping + reorder that the next FILL honors (the grouped plural-pointer survivor claimed first), answering a question from the board, pausing and resuming a running chunk from the board (its chip flips to `paused` over SSE with no reload, and back on resume — the claim-keeping per-chunk lever, distinct from the runner pause/resume brake covered next), and that runner-level brake itself.

Each holds at **both ends** — git truth on the bare origin and the hub's derived facts. The suite is **self-managed and token-free**: it mints its own disposable `blizzard-mock` fixture workspace, starts the real forge + hub + runner, and drives the reconciliation loop one synchronous tick at a time — every seam real (git over `file://`, the forge over HTTP, the `mock-claude-code` façade over its CLI). It needs the sibling **`blizzard-mock`** worktree provisioned (`winter provision <env>`) and a local winter source; scenario 6 also needs a Chromium (`uv run playwright install chromium`). Any scenario **skips cleanly** when its prerequisites are absent (e.g. a single-repo CI checkout, or no browser installed), so the default `uv run pytest` gate stays hermetic. To drive the same loop against the live tmux services instead, `winter service up <env> --wait` brings up forge + hub + runner (see the workspace's service manifest).

## The service tier (`mise run service-test`)

`mise run service-test` (`BLIZZARD_SERVICE=1 uv run pytest tests/service/`) is the
**service tier** (`blizzard-harness` `verification/blizzard.md`): one **running daemon's
HTTP API exercised from outside the process** with its counterpart bound to the mock
fleet — distinct from the e2e tier, which drives the whole loop with every seam real. It
lives in its own `tests/service/` package and, like e2e, is **skipped unless
`BLIZZARD_SERVICE=1`** and the sibling `blizzard-mock` worktree is provisioned, so the
default `uv run pytest` gate stays hermetic.

- **Runner service tests** (`test_runner_service.py`) drive the **real runner** loop
  (one synchronous tick at a time) against the **mock hub** (`blizzard-mock-hub`, run as
  its own subprocess), pulling its levers to manufacture states a real hub could only be
  contrived into: an **unreachable hub** proves the completion is store-and-forward
  buffered and lands on recovery; a **dropped ack** proves the re-flush re-applies
  idempotently through to done; a **stale envelope** is tolerated because the
  runner fences on its own lease epoch.
- **Hub service tests** (`test_hub_service.py`) drive the **real hub** with the **mock
  runner** (`blizzard-mock-runner`, a levered driver) and the **mock forge** as its
  counterparts, asserting over the wire: a claim + completion advances the chunk; the
  runner's `stale_epoch` lever gets the completion **rejected**; queue **grouping +
  reorder** are reflected in `peek`; and `GET /api/events/stream` serves the
  **SSE contract** an `EventSource` subscribes to.

The counterpart mocks and their lever surfaces live in the `blizzard-mock` repo
(`blizzard_mock.mock_hub` / `blizzard_mock.mock_runner`). sqlite only, no tokens, no
network.

## CI, build, and release

```bash
mise run gate    # the local equivalent of the PR-to-master merge gate
mise run build   # the one build entrypoint: Angular apps -> embed -> wheel -> verify install (node-free)
```

The GitHub Actions workflows (PR gate, push-to-master dev build, tag-`v*`
release) and the exact local commands equal to the gate are documented in
[docs/ci.md](./docs/ci.md).

## Deployment (colocated, under systemd)

A single machine runs both daemons — the hub and the supervisor (runner) side by
side. The systemd units live in [`packaging/systemd/`](./packaging/systemd/); the
install steps and the boot/crash recovery contract (how a reboot or a `kill -9`
comes back under systemd, reaps stale leases, and resumes each chunk at its
last-recorded node) are in [docs/deployment.md](./docs/deployment.md).
