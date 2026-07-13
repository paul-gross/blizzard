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
mise run e2e                   # the acceptance-loop e2e smoke test (see below)

blizzard hub init ./hub-data   # scaffold config + data dir + migrated DB (idempotent)
blizzard hub migrate           # apply pending store migrations (--down <rev> reverses)
blizzard hub host --dir ./hub-data   # serve; bare `blizzard hub` defaults to host

blizzard-export-openapi --out-dir openapi   # dump hub + runner OpenAPI specs
```

The same `init` / `migrate` / `host` verbs exist under `blizzard runner`. A daemon **refuses to start on a store-revision mismatch**, naming the exact `migrate` command (D-099, `bzh:manual-migrations`).

## The acceptance-loop e2e (`mise run e2e`)

`mise run e2e` (`BLIZZARD_E2E=1 uv run pytest tests/e2e/test_acceptance_loop.py`) is the standing end-to-end smoke test — the P6 exit criterion of `blizzard-discovery`'s `implementation/verification.md`. One chunk travels the whole lifecycle — ingest → acquire → mock-scripted commit → deliver → landed in the bare origin — and the assertion holds at **both ends**: the commit is reachable from the bare origin's `main` (git truth) and the hub's facts derive `done` (fleet truth).

It is **self-managed and token-free**: the test mints its own disposable `blizzard-mock` fixture workspace, starts the real forge + hub + runner, and drives the reconciliation loop one synchronous tick at a time — every seam real (git over `file://`, the forge over HTTP, the `mock-claude-code` façade over its CLI). It needs the sibling **`blizzard-mock`** worktree provisioned (`winter provision <env>`) and a local winter source; it **skips** when either is absent (e.g. a single-repo CI checkout), so the default `uv run pytest` gate stays hermetic. To drive the same loop against the live tmux services instead, `winter service up <env> --wait` brings up forge + hub + runner (see the workspace's service manifest).

## CI, build, and release

```bash
mise run gate    # the local equivalent of the PR-to-master merge gate
mise run build   # the one build entrypoint: Angular apps -> embed -> wheel -> verify install (node-free)
```

The GitHub Actions workflows (PR gate, push-to-master dev build, tag-`v*`
release) and the exact local commands equal to the gate are documented in
[docs/ci.md](./docs/ci.md).
