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
