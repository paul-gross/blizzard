# Strict Python typing baseline

At `blizzard` base revision `019ba7a8d15c4e4ab35125cf46b561f1c0b500eb`, after the Python service-test migration, Pyright
1.1.414 reports 17,040 strict errors with this branch's baseline checker present. The corresponding `blizzard-mock`
revision `f3a1f0425f37c8af94ef7ee029bcb9faf846498a` reports 1,633, and `blizzard-infra` revision
`a4c9bd2f979f1056b66061cb45e67cc8bf78177c` reports 96. These are diagnostic counts, not independent type-error roots: an
untyped fixture can cause multiple unknown-type reports. `blizzard-mock:typecheck` passes with zero errors because its
configured mode is `standard`; `blizzard:typecheck` holds only shipping code to strict (`bzh:strict-types`).

| Project / Python area                    | Strict errors | Files with errors |
| ---------------------------------------- | ------------: | ----------------: |
| blizzard root `tests/*.py`               |        16,486 |               664 |
| blizzard retained Python `tests/e2e`     |            39 |                 6 |
| blizzard retained Python `tests/service` |           140 |                 9 |
| blizzard `tests/rehearsal`               |            40 |                 4 |
| blizzard `tests/crash`                   |            19 |                 3 |
| blizzard `src/**/store/migrations/tests` |           305 |                51 |
| blizzard exemplar tests                  |             1 |                 1 |
| blizzard shipping `src`                  |            10 |                 3 |
| **blizzard total**                       |    **17,040** |           **741** |
| blizzard-mock `tests`                    |         1,302 |                28 |
| blizzard-mock `src`                      |           331 |                50 |
| **blizzard-mock total**                  |     **1,633** |            **78** |
| blizzard-infra `tests`                   |            96 |                 2 |

The `blizzard` measurement uses `mise run typecheck-strict --outputjson`. Its script retains the repository's
`[tool.pyright]` includes (`src`, `tests`, `exemplars/python`), excludes and extra paths, sets `typeCheckingMode=strict`
and `enableTypeIgnoreComments=false`, and calls `uv run pyright` on a generated config. It analyzes Python tests
including store migrations and exemplars, never Playwright TypeScript. The other two measurements use
`uv run pyright --project <transient-config> --outputjson`, with Pyright 1.1.414 installed transiently for infra. Both
transient configs set Python 3.12, strict mode, `enableTypeIgnoreComments=false`, `reportMissingTypeStubs=false`, and
point `venvPath`/`venv` at the project `.venv`. Their includes are respectively `src` plus `tests` and `tests` alone.
When the config is outside the repo, mock additionally needs the repo root and `src` in `extraPaths`; infra needs its
`tests` in `extraPaths` for the `pin_shape` import. Without those paths, unresolved local imports create false errors.
Infra has no standing Pyright method; its pytest/shellcheck static-contract gate does not check types.

`blizzard:strict-test-baseline` reconciles the three captured JSON reports. Run
`uv run python scripts/strict_test_baseline.py --blizzard <blizzard.json> --blizzard-mock <mock.json> --blizzard-infra <infra.json>`
from the blizzard root with the sibling worktrees present. The method holds this pinned snapshot to 2,730, 237 and 3
analyzed files respectively and the area totals in the table above, rejecting even a summary-consistent truncated
report. It checks every error's project, area, named rule, and cause category against the report summary. Its categories
distinguish diagnostic symptoms from root causes; the latter need investigation before a slice can promise a reduction.

The highest single-file budgets are `blizzard/tests/test_compat_wire.py` (460, untyped OpenAPI fixture-spec
dictionaries), `blizzard/tests/test_release_workflow.py` (347, parsed workflow/job dictionaries),
`blizzard-mock/tests/test_opencode_transcript_writer.py` (87, the `_read` JSON document),
`blizzard-infra/tests/test_deploy_contract.py` (76, parsed YAML/TOML dictionaries),
`blizzard-mock/src/blizzard_mock/mock_hub/domain/wire.py` (27, list `default_factory` inference), and
`blizzard-infra/tests/test_release_pin.py` (20, `list[dict[str, object]]` passed to `list[object]`). Each file total is
a scoped diagnostic budget, not a guarantee that one root correction removes every error. Re-run a scoped strict report
before each single-cause change. Scoped reports confirmed 460 for the compat-wire file, 473 for mock's
`test_harness_smoke.py`, and 20 for infra's release-pin file.

Private-name access is a separate interface concern: 794 blizzard sites and 30 mock sites. Group by the name reached
for, not merely by `reportPrivateUsage`. The larger groups are `_engine` (54 sites across 27 blizzard test files),
`_schema_snapshot` (27 store-migration files plus one exemplar test), and mock `_state` (12 sites across four test
files). Tests should use a published interface or establish that the reached name is legitimately public; a suppression
is not an interface repair. Fixture-parameter and bare-generic reports are later cause-specific sweeps rather than part
of the first single-file budgets.

The ten shipping-code errors this baseline counts are cleared, and the gate holds blizzard's `src/` and
`exemplars/python/` to strict. What it does not yet hold are the tests and each store migration test still carrying a
`# pyright: standard` waiver. Each follows the same path, per project: clear the remaining diagnostics or justify each
residual by file, Pyright rule, reason and removal condition, bring the area under the gate's strict paths, and retain
only rule-named `# pyright: ignore[<rule>]` comments with reasons. Infra first needs a standing typecheck task and
verification-matrix/CI declaration.
