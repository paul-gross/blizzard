# OpenCode compatibility

Use this procedure to run and interpret the runner's compatibility diagnostic for OpenCode. The runner admits a declared
semver *range* of OpenCode versions — currently `>=1.18.25,<2.0` — and the diagnostic checks the observed binary's
version for membership in that range, never equality against one pinned literal or membership in an enumerated list.
Pre-release versions are always excluded, regardless of whether they would otherwise fall inside the range. It changes
neither runner or hub state nor the caller's checkout: the proof creates a temporary initialized git repository, may
commit its scratch work there, and removes that repository when the run ends. The runner may copy the account's normal
OpenCode credential file byte-for-byte into an isolated disposable data root after the version gate; OpenCode receives
only that isolated copy, and the runner never parses or retains credential values. This is diagnostic evidence for an
external seam, not production adapter selection or a claim that a provider is available to a fleet. A pass is
established by the command's observed output and retained evidence, not by this page. Version preflight uses empty
isolated XDG data and provisions disposable auth only after the observed version is admitted.

## Before you run

Have these ready:

- A `blizzard` installation containing `runner opencode compatibility`.
- An OpenCode executable at an explicit path. The diagnostic observes its version and checks it for membership in the
  runner's admitted OpenCode version range (currently `>=1.18.25,<2.0`).
- The provider/model reference and variant to test. The model must use `provider/model` form.
- A working `git` executable and a writable evidence-directory path.
- OpenCode credentials available through OpenCode's normal credential discovery for the account running the command.
- On Linux, Landlock ABI 3 or newer; the process and model-tool filesystem boundaries fail closed when it is
  unavailable. The model-tool shell adds a second Landlock layer that excludes the credential root. The proof redirects
  disposable config, data, state, and cache writes and disables auto-update.
- Authorization for live provider calls and any resulting provider quota or spend.

The command has no token option. Its child environment is built from the runner allowlist, so arbitrary inherited
variables and daemon credential variables are not forwarded. Do not put credentials in command arguments, prompts, or
evidence that you plan to share.

## Required invocation

Every option below is required; the command has no defaults for this proof:

- `--binary PATH` — existing OpenCode executable file.
- `--model TEXT` — non-empty single argument in `provider/model` form.
- `--variant TEXT` — non-empty single argument naming the OpenCode variant.
- `--evidence-dir DIRECTORY` — writable path for sanitized evidence; missing parent directories are created.
- `--live-provider` or `--allow-live-provider` — required explicit opt-in to provider-reaching probes; can consume model
  tokens and quota or incur provider charges.

The live-provider option is a deliberate spending boundary. Do not add it until the provider call is authorized;
omitting it is a command error, not an offline mode.

For the `openai/gpt-5.6-luna` model and `max` variant, an invocation is:

```bash
umask 077
mkdir -p /var/tmp/blizzard-opencode-compatibility
blizzard runner opencode compatibility \
  --binary /path/to/opencode \
  --model openai/gpt-5.6-luna \
  --variant max \
  --evidence-dir /var/tmp/blizzard-opencode-compatibility \
  --live-provider
```

Replace the binary path, model, or variant when proving a different explicit input. The executable is never selected
from `PATH` implicitly; the command receives the path supplied by `--binary`.

## Offline rehearsal

Exercise this diagnostic's own process, allowlist, disposable-git, parser, report, and evidence machinery with no
provider quota spent, using a CLI-surface artifact the mock fleet emits in place of a live OpenCode binary. This
rehearses the procedure on this page and the diagnostic's machinery; it is not a live pass against real OpenCode and a
real provider — only the invocation above against the real binary establishes that.

1. From a provisioned `blizzard-mock` checkout, emit an artifact: `uv run mock-opencode emit --out <path>` — `uv run`
   resolves the console script through that checkout's own venv, so it works whether or not `mock-opencode` is on
   `PATH`.
2. Run the invocation above unchanged, with `--binary` pointed at the emitted path. `--live-provider` is still required
   — it is a policy opt-in, not a network switch, and the emitted artifact never reaches a real provider regardless.

A default rehearsal run reports `compatibility: degraded`, not `supported`: the emitted artifact has no root hook and no
child session for the diagnostic to observe, and those two probes' absence is exactly what degrades a report rather than
blocks it. Treat a rehearsal's `degraded`, `admissible: true` result as proof the diagnostic runs cleanly end to end,
never as evidence about real OpenCode or a real provider.

## Read the result

The command prints the observed version, one line for every required probe, and a final line in this shape:

```text
compatibility: supported|degraded|blocking
```

Each probe line has the shape `<name>: <classification> (<state>) — <summary>`. The required probe names and their
operator meaning are:

- `fresh_turn` — a fresh turn emits parsed events and a session export.
- `resume` — the existing session accepts a follow-up turn.
- `process_control` — a running OpenCode process can be interrupted and reaped.
- `judgement` — the resumed turn emits the explicit pass choice.
- `root_hook` — whether the proof has a portable root-hook lifecycle signal.
- `permission` — an unattended `opencode run --auto --format json --agent compatibility` call applies the runner-owned
  Bash deny rule and produces one explicit terminal denial; the configured model-tool shell, exercised directly, can
  neither read disposable auth nor mutate an external marker. `configuration_isolation` is what proves OpenCode resolves
  that shell for its own Bash tool.
- `model_variant` — the exported assistant message retains the requested model and variant.
- `usage_cost` — the export carries token usage and, when reported, an explicit cost.
- `takeover` — an interactive continuation enters the recorded scratch session with mini history replay disabled,
  submits one attended prompt, and observes that prompt in the requested session export.
- `transcript_read` — the session export parses with stable message and part identities.
- `transcript_cursor` — the identity cursor admits each exported message or part only once.
- `child_sessions` — the child-session response parses, or the CLI reports no child-session result. The proof denies the
  `task` tool for every agent, so a live run cannot spawn a child and this probe reports the neutral absence rather than
  an observation; its corpus fixture is hand-authored and pins the parser only.
- `configuration_isolation` — OpenCode externally enforces the runner-owned config outside the disposable project while
  competing project and user configs are isolated, and resolves both the runner-owned model-tool shell and the
  compaction tail bound the transcript proof depends on. The runner supplies the file through `OPENCODE_CONFIG` and
  reapplies its serialized contents through `OPENCODE_CONFIG_CONTENT`, which OpenCode loads after those competing
  scopes.

The deterministic policy is:

- `observed` makes a probe `supported`.
- `absent` is `degraded` only for `root_hook`, `usage_cost`, or `child_sessions`.
- `failed`, `ambiguous`, and every other `absent` result are `blocking`.
- A version outside the admitted range makes the report `blocking`, regardless of probe results.

The final classification is `supported` when the observed version is admitted and every probe is supported. It is
`degraded` when the observed version is admitted and only the allowed neutral absences occur. Both classifications exit
zero and are admissible under the policy; `blocking` exits one and is not admissible. A complete report contains every
required probe exactly once, and `report.json` records `complete`, `admissible`, and the admitted range the observed
version was judged against for this check.

## Evidence and failure handling

When a complete report is formed, the command writes these files under `--evidence-dir`:

- `report.json` — the observed version, final classification, completeness and admissibility, plus each classified
  probe.
- `runtime.json` — sanitized process observations and runtime metadata.

Use a fresh, private evidence directory. The writer redacts sensitive-key values and common
standalone/provider-prefixed, underscore, quoted, serialized, bearer, query-secret, and PEM forms, and replaces
disposable scratch, isolation, and evidence paths in retained values. Inspect both files before sharing them; never
share raw provider output or a credential alongside them.

The run copies the account's credential file into a temporary isolation root named `blizzard-opencode-isolation-*` under
the system temporary directory, mode `0600`. The runner removes that root when the run unwinds, including on error — but
not when the process is `SIGKILL`ed, the machine loses power, or the OOM killer fires. After any such abnormal end,
delete the leftover `blizzard-opencode-isolation-*` directory yourself before treating the host as clean.

Treat the run as failed or unusable when:

- required options are missing, `--binary` is not an existing file, or the explicit live opt-in is absent;
- the scratch repository or OpenCode process cannot complete, required output is malformed or unsupported, or evidence
  cannot be written;
- the report is incomplete, the observed version is not in the admitted range, or any probe is `blocking`.

Do not infer success from an individual OpenCode process exit or from a partial report. Use the final classification,
exit status, and the sanitized evidence together.

## Admitting a candidate version

This page's live diagnostic (above) runs against a version already inside the runner's admitted range —
`ADMITTED_OPENCODE_RANGE` (`src/blizzard/runner/harness/internal/opencode_probe.py`), a
`packaging.specifiers.SpecifierSet`. Admission is judged on the *normalized* observed version — the bare semantic
version `normalize_opencode_version` (`src/blizzard/runner/harness/internal/harness_shared.py`) extracts from the
binary's raw `--version` output — checked for range membership, never against the raw output text itself or as an
equality check against one pinned literal; a pre-release version is always excluded, and a version that fails to parse
reads as not admitted rather than raising.

Corpus fixtures stay pinned per exact version regardless:
`src/blizzard/runner/harness/contracts/opencode/<version>/manifest.json`. An observed version that falls inside the
range resolves to a *reference corpus* — the newest committed corpus version at or below it — so a candidate already
inside the range needs no code change at all to be admitted; it is judged offline against whichever committed corpus is
closest below it. Landing something new is owed only when either of the following is true, and the two are independent:

- **The candidate falls outside the current range.** Widening `ADMITTED_OPENCODE_RANGE`'s bound is what actually admits
  it — nothing else does.
- **The candidate needs its own evidence.** A version inside the range but behaviorally different enough from its
  current reference corpus (a probe's shape changed, a new declared degradation applies, and so on) needs a fresh corpus
  captured directly against it, so later versions resolve to *that* evidence instead of a stale one.

Either way, the same four-tier proof plus this page's own live diagnostic is owed before landing. Method ids below
(`blizzard:unit-test` and so on) are defined in blizzard-context's
[`verification/blizzard.md`](https://github.com/paul-gross/blizzard-context/blob/master/verification/blizzard.md).

1. On that exact candidate version, pass every earlier tier first:
   - `blizzard:unit-test` — the fixture suite: the committed
     `src/blizzard/runner/harness/contracts/opencode/<version>/manifest.json` and its captured probe fixtures classify
     cleanly for the candidate (`tests/test_runner_harness_offline_compatibility.py`,
     `tests/test_runner_harness_opencode_compatibility.py`).
   - `blizzard:component-test` — the generic OpenCode selftest: the runner's per-harness selftest checks
     (`tests/test_runner_selftest.py`) pass against the candidate's CLI surface, independent of the live compatibility
     diagnostic above.
   - `blizzard:service-test` — service integration: the candidate is exercised through the runner's own HTTP API
     (`tests/service/test_opencode_service.py`, `tests/service/test_opencode_compatibility_service.py`,
     `tests/service/test_mixed_harness_dispatch_service.py`).
   - `blizzard:crash-sweep` — crash verification: the candidate survives an unattended kill-9 at every registered
     OpenCode crash point (the `_OPENCODE_GENERIC_SWEEP` and `_OPENCODE_RESUME_SWEEP` points in
     `tests/crash/test_kill9_sweep.py`).
2. Only once those four pass does `blizzard:manual-opencode-compatibility` — this page's own live diagnostic — apply to
   the candidate. Run it per "Required invocation" above and read its result per "Read the result".
3. Land the range bound and/or the corpus fixture, whichever this candidate actually needs, together with its evidence:
   - Widening the range bound with no new corpus is only honest when the candidate genuinely reuses the nearest existing
     reference corpus's evidence unchanged.
   - A new corpus fixture must itself lie inside `ADMITTED_OPENCODE_RANGE` to ever be reachable as a reference corpus —
     the runner checks that at least one committed corpus lies inside the admitted range when it constructs the OpenCode
     health-probe binding, at daemon startup, and logs a warning rather than raising when none does; that degrades only
     the OpenCode binding's own health (`unknown_version`) rather than silently misclassifying or taking the whole
     daemon down. If this candidate's corpus has any known, non-blocking compatibility gap, declare it in the manifest's
     own `declared_degradations` key (each entry a `probe`/`summary` pair) — this is read gracefully, so a missing key
     means "none declared" and a malformed entry is dropped silently, with no separate warning either way; review the
     manifest by eye rather than relying on a failure to catch the gap.
4. Each earlier tier's own pass/fail is evidenced by that tier's own run output (pytest's, or CI's) — `report.json` (see
   "Evidence and failure handling" above) records only this page's own live diagnostic run: the observed version, its
   final classification, and completeness/admissibility. It does not record whether the four earlier tiers passed; there
   is no separate record-keeping mechanism for the admission decision as a whole beyond the commit that lands the range
   bound and/or corpus fixture.
