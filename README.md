<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/identity/logo-hubflake.svg">
    <img src="docs/identity/logo-hubflake-light.svg" alt="" width="96" height="96">
  </picture><br>
  Blizzard
</h1>

<p align="center">
  <strong>An orchestration platform for autonomous fleets of coding agents.</strong><br>
  Queue the work, walk away, come back to landed code — or to a precise escalation.
</p>

<p align="center">
  <a href="https://github.com/paul-gross/blizzard/releases"><img alt="Release" src="https://img.shields.io/github/v/release/paul-gross/blizzard?include_prereleases&sort=semver&color=f2b25c"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-%E2%89%A5%203.12-5cd1e5">
  <img alt="Store" src="https://img.shields.io/badge/store-sqlite%20%7C%20postgres-5cd1e5">
</p>

Blizzard runs **the loop around the work**. It ingests items from your backlog, sequences and claims them, leases each
worker an isolated environment in **your own workspace** ([winter](https://github.com/paul-gross/winter) enabled),
judges what comes back, drives the result to delivery, and recovers correctly when any of that is interrupted. That loop
— and the facts it records — is the whole product.

One **hub** is shared by everyone: a single queue, a single set of workflows, and one truthful account of what the fleet
has done. **Runners** are many — one on each engineer's machine, or on a spare box in the corner — all drawing from that
same queue. A team grows its fleet by adding machines rather than by coordinating calendars, and every engineer's agents
show up on the same board.

📚 **Operator docs:** [`docs/index.md`](./docs/index.md) · 🐳 **Start here:** [`docs/install.md`](./docs/install.md)

## ✨ Features

- **Unattended throughput** — queue work, close the laptop, come back to merged branches, chunks parked at exactly the
  human gates you configured, or precise escalations. Never to a wedged fleet.
- **Exactly-once delivery, structurally** — atomic leases and epochs, not retries and hope. No two agents ever hold the
  same chunk, and a reaped-but-still-running worker can never overwrite its successor's delivery.
- **Crash-equivalence** — `kill -9`, reboot, or power loss at any instant loses at most in-flight LLM tokens. Never
  queue state, never delivered work, never truthful status. There is a runnable demo of it in
  [`docs/deployment.md`](./docs/deployment.md).
- **Workflow graphs you author** — immutable YAML graphs of nodes, judgements, choices, and gates, with cycles for the
  fix loop. A packaged set ships in the box: a triage router that reads a chunk's work items and lands it in the lane
  that fits, plus lanes ranging from a compact build → review → deliver loop to a full plan → plan-review → build →
  verify → review → pre-push → deliver track.
- **Human gates are a dial, not a doctrine** — the baseline graph involves no human at all. Insert a gate node, or have
  a runner impose one by node name, and trust is tuned station by station as it is earned.
- **Flexible work shapes — never 1:1:1:1** — a chunk wraps one *or more* backlog items, one agent may fan out to
  subagents, and a single chunk may span several repositories and several feature environments at once.
- **Cheap human takeover** — when the fleet escalates, one pasted command drops you into the stuck agent's full session
  context, not a cold reconstruction of what it was doing.
- **Metered, boundable spend** — every attempt's token usage and cost is recorded as a fact and surfaced per chunk and
  fleet-wide, with an optional per-chunk cap and a runner-level spend kill-switch.
- **Mission control, embedded** — the board ships inside the wheel: a live fleet view with a mobile glance shell, a
  graph explorer with retire/re-enable controls, and a durable, severity-ranked operational event log.
- **Everything external is a seam** — workspace, work source, coding harness, delivery, and human channel are all named
  interfaces with pluggable providers. The reference stack is the first implementation, not a shortcut around them.
- **One repo, one wheel** — a single distributable ships both daemons, the CLI, and the compiled Angular frontend as
  embedded assets. No Node at install time or at runtime.
- **sqlite by default** — postgres is a configuration knob (`db_url`), not a prerequisite.

## 🚀 Quickstart

The fastest look at a running hub — install the wheel, scaffold a store, serve the board:

```bash
pip install https://github.com/paul-gross/blizzard/releases/download/v0.1.0-rc.1/blizzard-0.1.0rc1-py3-none-any.whl
blizzard hub init .          # scaffold config + data dir + a migrated sqlite store
blizzard hub host .          # serve the API + the embedded mission-control board
```

Then open <http://127.0.0.1:8421/> — the default port from the `blizzard-hub.toml` that `blizzard hub init` writes.

For anything past a first look, run the reference **container deployment** instead: hub, postgres, and a TLS-terminating
Caddy via `docker compose`, walked end to end in [`docs/install.md`](./docs/install.md). The alternative — a colocated
wheel + systemd install running both daemons side by side — is [`docs/deployment.md`](./docs/deployment.md).

Milestone builds are published as [GitHub Releases](https://github.com/paul-gross/blizzard/releases) with the wheel
attached and the image pushed; there is no package index for the wheel. Prerelease candidates are tagged `v0.1.0-rc.N`.
[`docs/versioning.md`](./docs/versioning.md) states what a version number promises and the supported hub↔runner skew.

## 🧩 How it works

**A chunk is the unit of work.** It wraps one or more items from your backlog by reference — the item's contents are
never copied into the hub — travels a workflow graph, and accumulates artifacts, questions, and decisions as it goes.
Nothing about its state is stored as a status: a chunk's current node derives from its newest accepted transition, and
its status derives from the facts recorded against it. Facts are append-only, so the truth survives every crash.

**The hub grants work; the runner does it.** The hub owns chunks, graphs, artifacts, and the runner registry, and it
never reaches into a developer's machine — all contact is runner-initiated. A runner claims a chunk, acquires the
environments it needs, drives a coding agent through one node-step at a time, and reports facts back. Operator controls
are declarative state rather than a command queue: pausing appends a fact, and the runner reads it on its own next
contact.

**Graphs are immutable and application-agnostic.** A graph declares the *shape* of the work — node roles, what each node
produces, how a verdict is rendered — never a toolchain. Every edit mints a new graph, so anything pinned to one can
trust it forever, and a chunk moves between graphs only through an explicit migration. The same graph drives twenty
unrelated applications unchanged.

**Delivery is deterministic and hub-executed.** The deliver node runs at the hub, not in an agent's shell: it merges to
the main branch in the baseline, or opens a pull request where a graph configures a human-review gate, and resolves when
that PR merges. Landed chunks close their work items back at their own source.

### What Blizzard deliberately isn't

Blizzard is **not** a build system, a test runner, or a code-review engine, and it holds no model of any application it
drives. That absence is a design position, not a gap.

Blizzard assumes a **competent agent dropped into a poly-repo capable workspace** can discover and follow the
conventions of the repos it finds there — how they build, how they test, what "verified" means, which surfaces a change
owes. A worker is leased a whole feature environment rather than a checkout, and one unit of work may span several repos
at once, so the repos are the only place those answers stay correct as toolchains diverge and change.

Two things follow, and they explain features you might otherwise expect to find:

- **There is no per-application configuration.** No repo-convention registry, no per-app graph variants, no place to
  tell Blizzard how your project is tested. If that seems missing, it is because the answer belongs in your repo, where
  your agents will read it.
- **There is no second backlog.** The work source owns what work *is*; the hub's chunks carry execution state and a
  workflow position, never a competing definition of the task.

What Blizzard does own is everything an agent cannot be trusted to do by being competent: exactly-once delivery, crash
recovery at any step boundary, fencing a zombie worker out of the merge queue, metering spend, and keeping a truthful
account of what happened.

## 🔌 Seams and the reference stack

Interoperability is the core of the design. Every external dependency is a named seam with a provider behind it — the
reference binding is the first implementation of the interface, never a shortcut around it.

| Seam               | What plugs in                                             | Reference binding                                                   |
| ------------------ | --------------------------------------------------------- | ------------------------------------------------------------------- |
| **Workspace**      | Provides isolated, poly-repo execution environments       | [winter](https://github.com/paul-gross/winter) feature environments |
| **Work source**    | The system holding the backlog, ingested by item id       | GitHub issues                                                       |
| **Coding harness** | The agent that actually does the work                     | Claude Code                                                         |
| **Workflow**       | How work moves — graphs of nodes, judgements, and gates   | Hub-defined YAML workflow graphs                                    |
| **Delivery**       | Integrates finished work, executed at the hub             | Merge to the main branch, or a pull request at a gate               |
| **Human channel**  | Reaches people for questions, escalations, and visibility | The mission-control board                                           |

Winter matters more than the other bindings, and deliberately so: an orchestrator is only as capable as the chunks its
agents can safely hold. A winter feature environment composes one git worktree per project repository on a shared
branch, with its own ports and running services — which is what makes the many-to-many chunk executable at all. Two
agents on different chunks never share a working tree, never collide on ports, never trip over each other's services.

### Sibling repos

- **[blizzard-context](https://github.com/paul-gross/blizzard-context)** — the conventions harness every change here is
  held to: the domain model, the architecture rules, the code standards, and the verifiability matrix.
- **[blizzard-mock](https://github.com/paul-gross/blizzard-mock)** — the mock fleet: mock coding harnesses, a mock
  forge, mock hub and runner counterparts, and the mock-data CLI that the upper test tiers run against.

## 🧭 Principles

- **Deterministic shell, intelligent core.** The queue, the lease protocol, the reconciliation loop, the fencing, and
  the crash recovery are ordinary deterministic code. Models are invited in only where judgment is genuinely the job. An
  LLM can be wrong in judgment and the system survives it; it is never handed a lever that lets it be wrong in
  arithmetic.
- **Facts, not status.** Nothing observable is a stored flag. A chunk's status, a runner's liveness, and every brake are
  derived from an append-only fact log, which is why `kill -9` costs at most in-flight tokens.
- **Application-agnostic graphs.** A workflow declares the shape of work, never a toolchain.
- **Screaming architecture.** The top-level packages announce what Blizzard *is* — two daemons and the client that
  speaks to them.
- **Interoperable by construction.** Swapping a binding is an adapter's worth of work, never a rewrite.

## 🛠️ Development

The top-level packages:

| Package                    | What it is                                                                                                                                                                                                                                                                                              |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/blizzard/hub/`        | the `blizzard-hub` daemon — the work orchestrator. `api/` HTTP edge, `domain/` core, `store/` with its **own** Alembic tree. Prompt-authoring conventions for the packaged graphs: [graphs/advanced-development-workflow/README.md](./src/blizzard/hub/graphs/advanced-development-workflow/README.md). |
| `src/blizzard/runner/`     | the `blizzard-runner` daemon — the supervisor. The same `api/` + `domain/` + `store/` shape, over an **independent** Alembic tree. Its `harness/prompts/` tree follows the same [prompt-authoring conventions](./src/blizzard/hub/graphs/advanced-development-workflow/README.md).                      |
| `src/blizzard/cli/`        | the `blizzard` binary's root command group — verbs namespaced by target (`blizzard hub …`, `blizzard runner …`).                                                                                                                                                                                        |
| `src/blizzard/foundation/` | the shared kernel both daemons compose: the injected clock, structlog wiring, the portable store engine, and the Alembic migration runner plus its revision-mismatch guard.                                                                                                                             |
| `src/blizzard/static/`     | the wheel-embedded frontend assets seam — CI fills `hub/` and `runner/` with the compiled Angular apps ([static/README.md](./src/blizzard/static/README.md)).                                                                                                                                           |
| `src/blizzard/tools/`      | dev and CI tooling — the OpenAPI exporter (`blizzard-export-openapi`).                                                                                                                                                                                                                                  |

```bash
uv sync                        # install
uv run ruff check .            # lint
uv run ruff format --check .   # format
uv run pyright                 # typecheck
uv run pytest                  # unit + component tiers — hermetic and token-free
mise run gate                  # the local equivalent of the PR-to-master merge gate
mise run build                 # Angular apps -> embed -> wheel -> verify install (node-free)
```

A daemon **refuses to start on a store-revision mismatch**, naming the exact `migrate` command to run — migrations are
never applied implicitly at startup.

### The upper test tiers

Both are skipped unless explicitly enabled, so the default `uv run pytest` gate stays hermetic and token-free.

```bash
mise run service-test   # one running daemon's HTTP API, exercised from outside the process,
                        # with its counterpart bound to the mock fleet
mise run e2e            # the standing end-to-end smoke suite — every seam real
```

`mise run e2e` mints its own disposable `blizzard-mock` fixture workspace, starts a real forge, hub, and runner, and
drives the reconciliation loop one synchronous tick at a time — git over `file://`, the forge over HTTP, the coding
harness behind its real CLI façade. Every delivery scenario is asserted at **both ends**: git truth on the bare origin
*and* the hub's derived facts. It needs a provisioned sibling `blizzard-mock` worktree, and the browser scenarios need a
Chromium (`uv run playwright install chromium`); any scenario whose prerequisites are absent skips cleanly.

What each tier proves, scenario by scenario, is owned by the
[verification matrix](https://github.com/paul-gross/blizzard-context/blob/master/verification/blizzard.md) in
`blizzard-context` — read there rather than here. The CI workflows and the exact local commands equal to the merge gate
are in [`docs/ci.md`](./docs/ci.md).

## 💭 Why "Blizzard"?

A blizzard is a great many flakes moving as one system — which is the shape of the product, and the shape of the mark:
the **hub-flake**, a snowflake that is secretly an orchestration graph, with an amber hub at the center and a cyan agent
node capping each spoke. The name also carries its lineage: Blizzard is built with, and takes its reference workspace
binding from, [winter](https://github.com/paul-gross/winter).

## Contributing

Issues, bug reports, and ideas are welcome from anyone, any time. For changes to Blizzard itself, open an issue
introducing what you'd like to work on before investing in a PR, so we can align on direction — the conventions any
change is held to live in [blizzard-context](https://github.com/paul-gross/blizzard-context), and a change is expected
to arrive proven against its verifiability matrix.
