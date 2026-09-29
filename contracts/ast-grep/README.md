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
  - Scoped to `src/blizzard/hub/domain/**` and `src/blizzard/runner/domain/**` — a domain operation's own home, not the
    whole repo.
  - A domain *operation* is a public method; a leading-underscore helper is an internal step, not an edge-facing entry
    point, and is exempt even when it shares the same id-in, load-by-id shape.
  - Matches a public method taking a `*_id: str` parameter whose body calls a `get_`/`load_`-prefixed method on `self.*`
    with that same parameter, by exact textual identity — a body that rebinds the identifier to a new name before
    passing it on slips past. `ast-grep` has no taint mode; this rule is a floor, not a proof.
  - One exemption stands, at `ClaimService.claim` (`src/blizzard/hub/domain/claim.py`), reasoned at the site with
    `# ast-grep-ignore: bzh:domain-takes-objects` immediately above the `def`.

- **`bzh:property-delegates`** (`rules/property-delegates.yml`) — a property body only delegates. The rule's own prose
  home is `blizzard-context:/standards/python.md#a-property-body-only-delegates-bzhproperty-delegates`; this file states
  none of that prose, only what the rule mechanically checks.
  - Scoped to `src/blizzard/**`.
  - Matches a function decorated with `@property`, `@cached_property`, `@functools.cached_property`, or
    `@<name>.setter` / `@<name>.deleter` — alone or stacked — whose body holds, at any depth, an `if`, conditional
    expression, `match`, comprehension `if`, `and`/`or`/`not`, or comparison. A plain method and a lone
    `@staticmethod`/`@classmethod` are unmatched.
  - Every site that predates the rule is recorded debt, allowlisted by a trailing `# ast-grep-ignore: bzh:property-delegates`
    on its `def` line. Under `--error=unused-suppression` the list only shrinks.

- **`bzh:subscriptions-no-write`** (`rules/subscriptions-no-write.yml`) — blizzard never opens a subscription credential
  file for writing: the vendor CLI owns its own lock, atomic write, and refresh-token rotation, and a second writer
  risks corrupting the file mid-refresh or invalidating the login it just renewed.
  - Scoped to `src/blizzard/runner/subscriptions/**` — every sampler and renewer binding's own home.
  - Matches `.write_text(`, `.write_bytes(`, a bare or keyword-carrying `open($PATH, $MODE, ...)` whose mode string
    contains `w`, `a`, or `x`, and the same shape via `$PATH.open($MODE)`. A read (`open(path)`, `open(path, "r")`,
    `path.read_text()`) is unmatched.
  - No exemption stands; nothing in `runner/subscriptions/` opens a credential file for writing today.

- **`bzh:store-exclusive-write`** (`rules/store-exclusive-write.yml`) — an in-process lock in `hub/` cannot enforce an
  exactly-one-wins decision once more than one hub process shares a store. The rule's own prose home is
  `blizzard-context:/architecture/system-shape/exclusive-writes.md`; this file states none of that prose, only what the
  rule mechanically checks.
  - Scoped to `src/blizzard/hub/**` — the whole hub, not just its domain layer.
  - Matches a bare `import threading` — an injected lock arrives constructed elsewhere, but every holder still imports
    the module to type its own field, so the import is the one textual signal common to both shapes.
  - Every current holder — `app.py`, `composition.py`, and the domain's `dependencies.py`, `queue.py`, and `delete.py`,
    each for the one residual fleet-wide cycle-check lock a row lock cannot close — carries an `ast-grep-ignore`
    comment at its import, reasoned at the site as recorded debt. `claim.py`, `edit.py`, and `restart.py` migrated
    fully onto the row lock and import `threading` no longer.
