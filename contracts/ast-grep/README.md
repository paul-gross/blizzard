# The structural-gate channel

`ast-grep` rules that gate a structural invariant no type-checker or test suite reaches directly — matched against real
Python syntax rather than text. `rules/` holds one YAML file per rule; `rule-tests/` pins each rule's positive and
negative shape with `ast-grep test`'s own `valid:`/`invalid:` fixtures and snapshots.

`sgconfig.yml` at the repo root points here (`ruleDirs`/`testConfigs`); `mise run structural-gate` and
`scripts/ci-gate.sh` both run `ast-grep scan --error=unused-suppression .` — the `--error=unused-suppression` flag is
load-bearing: without it a stale `# ast-grep-ignore` comment goes unreported instead of failing the gate — then
`ast-grep test`, so a `rule-tests/` fixture or snapshot regression fails the same gate as a live violation.

## Rules

- **`bzh:domain-takes-objects`** (`rules/domain-takes-objects.yml`) — a domain operation takes an already-loaded object,
  never a raw identifier it resolves itself. The rule's own prose home, including its `Detect`/`Scope` boundary, is
  `blizzard-context:/architecture/repository-access.md#domain-operations-take-objects-bzhdomain-takes-objects`; this
  file states none of that prose, only what the rule mechanically checks:
  - Scoped to `src/blizzard/hub/domain/**` and the runner's domain core — every module of a runner layer node outside
    an `internal/` package, one `files:` glob per top-level node with `**/internal/**` ignored — a domain operation's
    own home, not the whole repo. `tests/test_layering.py` holds the runner globs equal to the layer table's nodes.
  - A domain *operation* is a public method; a leading-underscore helper is an internal step, not an edge-facing entry
    point, and is exempt even when it shares the same id-in, load-by-id shape.
  - Matches a public method taking a `*_id: str` parameter whose body calls a `get_`/`load_`-prefixed method on `self.*`
    with that same parameter as its first argument, alone or with further arguments, by exact textual identity — a body
    that rebinds the identifier to a new name before passing it on slips past. `ast-grep` has no taint mode; this rule
    is a floor, not a proof.
  - One exemption stands, at `ClaimService.claim` (`src/blizzard/hub/domain/execution/claim.py`), reasoned at the site with
    `# ast-grep-ignore: bzh:domain-takes-objects` immediately above the `def`.

- **`bzh:cli-httpx-confined`** (`rules/cli-httpx-confined.yml`) — the CLI trees import httpx only in
  `src/blizzard/cli/collaborators.py`, which builds the client factory and re-exports the httpx types the commands name.
  The rule's own prose home is `blizzard-context:/architecture/clean-architecture.md` § Dependency injection; this file
  states none of that prose, only what the rule mechanically checks.
  - Scoped to `src/blizzard/cli/**`, `src/blizzard/hub/cli/**` and `src/blizzard/runner/cli/**`, except
    `src/blizzard/cli/collaborators.py`.
  - Matches `import httpx`, `import httpx as …`, `from httpx import …` and `from httpx.<module> import …`, including
    under `TYPE_CHECKING`.

- **`bzh:cli-patch-not-seam`** (`rules/cli-patch-not-seam.yml`) — a test does not patch a name inside a CLI module by its
  dotted path. The rule's own prose home is `blizzard-context:/architecture/clean-architecture.md` § Dependency
  injection; this file states none of that prose, only what the rule mechanically checks.
  - Scoped to `tests/**`.
  - Matches a `setattr` call whose first argument is a string literal naming a `blizzard.` path with a `cli` segment.

- **`bzh:config-codec`** (`rules/config-codec.yml`) — PyYAML is imported only by the strict YAML binding of the config
  codec. The rule's own prose home is `blizzard-context:/architecture/system-shape/configuration.md`; this file states
  none of that prose, only what the rule mechanically checks.
  - Scoped to `src/blizzard/**`, except `src/blizzard/hub/documents/internal/**`.
  - Matches `import yaml`, `import yaml as …`, `from yaml import …`, and `from yaml.<module> import …`.

- **`bzh:config-key-declared`** (`rules/config-key-declared.yml`) — a daemon reads a config document's keys only through
  its declared registry. The rule's own prose home is `blizzard-context:/architecture/system-shape/configuration.md`;
  this file states none of that prose, only what the rule mechanically checks.
  - Scoped to `src/blizzard/runner/**` and `src/blizzard/hub/**`, except `src/blizzard/runner/host_config.py`,
    `src/blizzard/hub/config.py` and `src/blizzard/hub/domain/runners/version_window.py`.
  - Matches `import tomllib`, `import tomllib as …` and `from tomllib import …`.

- **`bzh:runner-child-launch`** (`rules/runner-child-launch.yml`) — every child the runner launches goes through the
  launch seam in `runner/process`. The rule's own prose home is
  `blizzard-context:/architecture/system-shape/worker-boundary.md`; this file states none of that prose, only what the
  rule mechanically checks.
  - Scoped to `src/blizzard/runner/**`, `src/blizzard/foundation/**` and `src/blizzard/hub/**`, except
    `src/blizzard/runner/process/**` (the seam and its driver), `src/blizzard/hub/delivery/internal/hub_command_runner.py`
    (the hub's one launch site) and the colocated `store/migrations/tests/**` trees, which are test code.
  - Matches `import subprocess`, `import pty` (bare or aliased), and `from subprocess import …` / `from pty import …`.
    `asyncio.create_subprocess_*`, `os.spawn*`, `os.system` and `os.fork` are not matched — a floor, not a proof.
  - No exemption stands.

- **`bzh:property-delegates`** (`rules/property-delegates.yml`) — a property body only delegates. The rule's own prose
  home is `blizzard-context:/standards/python.md#a-property-body-only-delegates-bzhproperty-delegates`; this file states
  none of that prose, only what the rule mechanically checks.
  - Scoped to `src/blizzard/**`.
  - Matches a function decorated with `@property`, `@cached_property`, `@functools.cached_property`, or `@<name>.setter`
    / `@<name>.deleter` — alone or stacked — whose body holds, at any depth, an `if`, conditional expression, `match`,
    comprehension `if`, `and`/`or`/`not`, or comparison. A plain method and a lone `@staticmethod`/`@classmethod` are
    unmatched.
  - Every site that predates the rule is recorded debt, allowlisted by a trailing
    `# ast-grep-ignore: bzh:property-delegates` on its `def` line. Under `--error=unused-suppression` the list only
    shrinks.

- **`bzh:subscriptions-no-write`** (`rules/subscriptions-no-write.yml`) — blizzard never opens a subscription credential
  file for writing: the vendor CLI owns its own lock, atomic write, and refresh-token rotation, and a second writer
  risks corrupting the file mid-refresh or invalidating the login it just renewed.
  - Scoped to `src/blizzard/runner/subscriptions/**` — every sampler and renewer binding's own home.
  - Matches `.write_text(`, `.write_bytes(`, `open($PATH, $MODE, ...)` and `$PATH.open($MODE, ...)` (further arguments
    allowed) whose mode string contains `w`, `a`, `x`, or `+`, any `os.open` carrying a write flag (`O_WRONLY`,
    `O_RDWR`, `O_CREAT`, `O_APPEND`, `O_TRUNC`), and any `os.replace` or `os.rename`. A read (`open(path)`,
    `open(path, "r")`, `path.read_text()`, `os.open(path, os.O_RDONLY)`) is unmatched. A keyword `mode=` and `os.fdopen`
    are not matched — a floor, not a proof.
  - No exemption stands; nothing in `runner/subscriptions/` opens a credential file for writing today.

- **`bzh:store-exclusive-write`** (`rules/store-exclusive-write.yml`) — an in-process lock in `hub/` cannot enforce an
  exactly-one-wins decision once more than one hub process shares a store. The rule's own prose home is
  `blizzard-context:/architecture/system-shape/exclusive-writes.md`; this file states none of that prose, only what the
  rule mechanically checks.
  - Scoped to `src/blizzard/hub/**` — the whole hub, not just its domain layer.
  - Matches a bare `import threading` — an injected lock arrives constructed elsewhere, but every holder still imports
    the module to type its own field, so the import is the one textual signal common to both shapes.
  - Every current holder — `app.py`, `composition.py`, and the domain's `dependencies.py`, `queue.py`, and `delete.py`,
    each for the one residual fleet-wide cycle-check lock a row lock cannot close — carries an `ast-grep-ignore` comment
    at its import, reasoned at the site as recorded debt. `claim.py`, `edit.py`, and `restart.py` migrated fully onto
    the row lock and import `threading` no longer.

- **`bzh:store-no-for-update`** (`rules/store-no-for-update.yml`) — a guarded write never takes its lock with
  `.with_for_update()`: it renders nothing on SQLite. The prose home is the `bzh:store-exclusive-write` rule's **Do**
  in `blizzard-context:/architecture/system-shape/exclusive-writes.md` (a no-op `UPDATE` as the first statement).
  - Scoped to `src/blizzard/hub/**`; matches any `$X.with_for_update($$$)` call.

- **`bzh:store-newest-read-limited`** (`rules/store-newest-read-limited.yml`) — a newest-per-key store read bounds its
  statement with `.limit(...)` rather than pulling a key's whole history to keep one row. The related rule is
  `bzh:newest-per-key-read`; this rule covers only the crisp sub-shape below and does not replace it.
  - Scoped to `src/blizzard/hub/store/internal/**` and `src/blizzard/runner/store/internal/**`.
  - Matches a function that contains a `.desc()` ordering and a `.first()` or `[0]` take and contains no `.limit(...)`.
    The Python-fold variant (select all rows, overwrite into a dict) and the walk-to-first-non-retired variant are not
    matched — a floor, not a proof.
  - No exemption stands.

- **`bzh:seam-answers-binding-facts`** (`rules/seam-answers-binding-facts.yml`) — a binding's config name is compared only at
  its selection point; every other consumer asks the seam for the binding's fact (`IWorkspaceProvider`, the harness
  catalog's declarations). The rule's own prose home is `blizzard-context:/architecture/system-shape.md`; this file
  states none of that prose, only what the rule mechanically checks:
  - Scoped to `src/blizzard/**`, excluding the selection points, the bindings' own modules and every `migrations/`
    tree — the rule file's `ignores` list them. The harness ids in its `ignores` and its regexes are held to the
    catalog's by a unit test (`test_layering.py`), so the rule names no binding this file could drift from.
  - Matches a comparison (`==`, `!=`, `in`, `not in`, …) with a `workspace_provider` name or attribute as an operand, or
    with a harness-id string literal as an operand, directly or inside a literal set, tuple or list. A registry lookup keyed by the name is unmatched. A name rebound to another variable before the
    comparison slips past — a floor, not a proof.
  - No exemption stands.

- **`bzh:statement-scoped-streaming`** (`rules/statement-scoped-streaming.yml`) — streaming is requested per statement,
  never on a connection, where it outlives the read and breaks the writes that follow it on Postgres. The prose home is
  the `bzh:sql-portable` rule's **Detect** in `blizzard-context:/standards/persistence.md`.
  - Scoped to `src/**`.
  - Matches any `.execution_options(...)` call passing `yield_per=` or `stream_results=` as a keyword, through a
    dict-literal splat with a string-literal key (`**{"yield_per": n}`), or through an opaque splat (`**opts`, any `**`
    operand that is not a dict literal), whose keys cannot be seen. The one admitted form is the
    `execution_options={...}` argument of a single `execute(...)`. The rule is syntactic and cannot tell a connection
    receiver from a statement receiver, so it admits only that form.
  - Declared limit: a dict-literal splat keyed by a named constant (`**{_SCOPE_OPTION: …}`, as `src/` uses) is admitted,
    because a syntactic rule cannot resolve the constant. A constant naming `yield_per` or `stream_results` is the
    residual lookalike.
  - No exemption stands.
