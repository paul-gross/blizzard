# Blizzard

The main application — the **hub**, the **runner**, the **CLI**, and the web board for orchestrating autonomous fleets of coding agents.

One repo, one wheel (D-061): the single distributable ships both daemons, the CLI, and the compiled frontend as embedded assets — no Node at install or runtime ([blizzard-discovery `implementation/build.md`](https://github.com/paul-gross/blizzard-discovery)).

## Layout (screaming architecture — `bzh:screaming-architecture`)

The top-level packages announce what blizzard *is*: two daemons and the client that speaks to them.

| Package | What it is |
|---------|-----------|
| `src/blizzard/hub/` | the `blizzard-hub` daemon — the work orchestrator. `api/` HTTP edge, `domain/` core, `store/` with its **own** Alembic tree. |
| `src/blizzard/runner/` | the `blizzard-runner` daemon — the supervisor. Same `api/` + `domain/` + `store/` shape, an **independent** Alembic tree (D-099). |
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
mise run e2e                   # the standing e2e smoke suite — six full-stack scenarios (see below)

blizzard hub init ./hub-data   # scaffold config + data dir + migrated DB (idempotent)
blizzard hub migrate           # apply pending store migrations (--down <rev> reverses)
blizzard hub host --dir ./hub-data   # serve; bare `blizzard hub` defaults to host

blizzard-export-openapi --out-dir openapi   # dump hub + runner OpenAPI specs
```

The same `init` / `migrate` / `host` verbs exist under `blizzard runner`. A daemon **refuses to start on a store-revision mismatch**, naming the exact `migrate` command (D-099, `bzh:manual-migrations`).

## The standing e2e smoke suite (`mise run e2e`)

`mise run e2e` (`BLIZZARD_E2E=1 uv run pytest tests/e2e/`) is the standing end-to-end smoke suite — the acceptance criterion of `blizzard-discovery`'s `implementation/verification.md`. It grew from the P6 acceptance loop to **six** full-stack scenarios over the `build → review → deliver` default shape and its human-loop and operator-surface variants:

1. `test_acceptance_loop` — the happy path: one chunk travels ingest → acquire → mock-scripted commit → review (PASS) → deliver → landed on bare `main`;
2. `test_review_cycle_e2e` — review fails once, the findings + prompt_addendum thread back into build, then it lands on the second pass;
3. `test_escalation_e2e` — two verdict-less exits exhaust the retry budget, the chunk derives `needs_human`, and the surfaced takeover command resumes the parked session;
4. `test_ask_answer_e2e` — a build worker asks and parks `waiting_on_human`; `blizzard hub answer` resumes the dormant session and it lands;
5. `test_gate_decision_e2e` — a human `approve-gate` parks a Decision; `blizzard hub decide` approves and it delivers;
6. `test_board_browser_e2e` — the **browser tier**: a real Chromium (Playwright) drives the served mission-control board — a live status chip that flips over SSE with no reload, the detail drawer's history + artifacts, queue grouping + reorder that the next FILL honors (the grouped plural-pointer survivor claimed first), answering a question from the board, and the runner pause/resume brake.

Each holds at **both ends** — git truth on the bare origin and the hub's derived facts. The suite is **self-managed and token-free**: it mints its own disposable `blizzard-mock` fixture workspace, starts the real forge + hub + runner, and drives the reconciliation loop one synchronous tick at a time — every seam real (git over `file://`, the forge over HTTP, the `mock-claude-code` façade over its CLI). It needs the sibling **`blizzard-mock`** worktree provisioned (`winter provision <env>`) and a local winter source; scenario 6 also needs a Chromium (`uv run playwright install chromium`). Any scenario **skips cleanly** when its prerequisites are absent (e.g. a single-repo CI checkout, or no browser installed), so the default `uv run pytest` gate stays hermetic. To drive the same loop against the live tmux services instead, `winter service up <env> --wait` brings up forge + hub + runner (see the workspace's service manifest).

## CI, build, and release

```bash
mise run gate    # the local equivalent of the PR-to-master merge gate
mise run build   # the one build entrypoint: Angular apps -> embed -> wheel -> verify install (node-free)
```

The GitHub Actions workflows (PR gate, push-to-master dev build, tag-`v*`
release) and the exact local commands equal to the gate are documented in
[docs/ci.md](./docs/ci.md).
