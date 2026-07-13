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
uv run pytest                  # unit tier

blizzard hub init ./hub-data   # scaffold config + data dir + migrated DB (idempotent)
blizzard hub migrate           # apply pending store migrations (--down <rev> reverses)
blizzard hub host --dir ./hub-data   # serve; bare `blizzard hub` defaults to host

blizzard-export-openapi --out-dir openapi   # dump hub + runner OpenAPI specs
```

The same `init` / `migrate` / `host` verbs exist under `blizzard runner`. A daemon **refuses to start on a store-revision mismatch**, naming the exact `migrate` command (D-099, `bzh:manual-migrations`).

## CI, build, and release

```bash
mise run gate    # the local equivalent of the PR-to-master merge gate
mise run build   # the one build entrypoint: Angular apps -> embed -> wheel -> verify install (node-free)
```

The GitHub Actions workflows (PR gate, push-to-master dev build, tag-`v*`
release) and the exact local commands equal to the gate are documented in
[docs/ci.md](./docs/ci.md).
