# OpenCode compatibility

Use this procedure to run and interpret the runner's compatibility diagnostic for OpenCode. The diagnostic reads the
binary's version and runs the proof for its major version: OpenCode 1 (`>=1.18.25,<2.0`) is described first, and
OpenCode 2 (`>=2.0,<3.0`) in [OpenCode 2](#opencode-2) below. A version neither range admits is reported as outside
every admitted range, and nothing is probed. For OpenCode 1, the runner admits a declared semver *range* of versions and
the diagnostic checks the observed binary's version for membership in that range, never equality against one pinned
literal or membership in an enumerated list. Pre-release versions are always excluded, regardless of whether they would
otherwise fall inside the range. It changes neither runner or hub state nor the caller's checkout: the proof creates a
temporary initialized git repository, may commit its scratch work there, and removes that repository when the run ends.
The runner may copy the account's normal OpenCode credential file byte-for-byte into an isolated disposable data root
after the version gate; OpenCode receives only that isolated copy, and the runner never parses or retains credential
values. This is diagnostic evidence for an external seam, not production adapter selection or a claim that a provider is
available to a fleet. A pass is established by the command's observed output and retained evidence, not by this page.
Version preflight uses empty isolated XDG data and provisions disposable auth only after the observed version is
admitted.

## Before you run

This section and the next five are for OpenCode 1; OpenCode 2 has its own in [OpenCode 2](#opencode-2). Have these
ready:

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

The compatibility diagnostic checks configuration acceptance, not whether a numeric compaction window causes a session
to compact at its intended threshold. That separate behavioral proof is
[`blizzard:manual-autocompact-window`](https://github.com/paul-gross/blizzard-context/blob/master/verification/blizzard/manual.md#blizzardmanual-autocompact-window);
retain its evidence independently of this diagnostic's report.

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
  an observation; its corpus fixture is hand-authored and pins the parser only. A separate 1.18.32 live capture, beside
  the corpus and without a manifest, pins the real `task` shape (`metadata.sessionId`, `input.subagent_type`, tool and
  message times) the usage fold and the transcript source read.
- `configuration_isolation` — OpenCode externally enforces the runner-owned config outside the disposable project while
  competing project and user configs are isolated, and resolves both the runner-owned model-tool shell and the
  compaction tail bound the transcript proof depends on. The runner supplies the file through `OPENCODE_CONFIG` and
  reapplies its serialized contents through `OPENCODE_CONFIG_CONTENT`, which OpenCode loads after those competing
  scopes.

The deterministic policy is:

- `observed` makes a probe `supported`.
- `absent` is `degraded` only for `root_hook`, `usage_cost`, or `child_sessions`.
- `failed`, `ambiguous`, and every other `absent` result are `blocking`.
- A version outside every admitted range is refused before any probe runs: the command prints `OpenCode version:` and a
  `compatibility: blocking — outside every admitted range` line naming both ranges, writes no evidence, and exits one.

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
- the observed version is outside every admitted range (nothing is probed or written), the report is incomplete, or any
  probe is `blocking`.

Do not infer success from an individual OpenCode process exit or from a partial report. Use the final classification,
exit status, and the sanitized evidence together.

## Admitting a candidate version

This section covers OpenCode 1. OpenCode 2 admits by `ADMITTED_OPENCODE_V2_RANGE`
(`src/blizzard/runner/harness/opencode_v2/version.py`), normalized by `normalize_opencode_v2_version`; its corpus lives
at the same `src/blizzard/runner/harness/contracts/opencode/<version>/` path with `"major_version": 2` in its manifest,
and `tests/unit/runner/harness/opencode_v2/compatibility/test_proof.py` and
`tests/service/harnesses/test_opencode2_compatibility_service.py` judge it. A version an operator relies on has also
passed OpenCode 2's release gate on a workstation running it: this proof ending `supported` or `degraded`,
`blizzard runner selftest opencode` on the operator's service, a concurrency soak of fleet turns beside an operator
session, and one real chunk run end to end on a local hub. Its corpus's `release-gate.json`, beside the manifest the
proof writes, records the version, the date it passed, the model, and each check's result — the proof's classification,
the others `passed`.

This page's live diagnostic (above) runs against a version already inside the runner's admitted range —
`ADMITTED_OPENCODE_RANGE` (`src/blizzard/runner/harness/opencode/compatibility/probe.py`), a
`packaging.specifiers.SpecifierSet`. Admission is judged on the *normalized* observed version — the bare semantic
version `normalize_opencode_version` (`src/blizzard/runner/harness/opencode/version.py`) extracts from the binary's raw
`--version` output — checked for range membership, never against the raw output text itself or as an equality check
against one pinned literal; a pre-release version is always excluded, and a version that fails to parse reads as not
admitted rather than raising.

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
     cleanly for the candidate (`tests/unit/runner/harness/test_offline_compatibility.py`,
     `tests/unit/runner/harness/opencode/test_compatibility.py`).
   - `blizzard:component-test` — the generic OpenCode selftest: the runner's per-harness selftest checks
     (`tests/component/runner/test_selftest.py`) pass against the candidate's CLI surface, independent of the live
     compatibility diagnostic above.
   - `blizzard:service-test` — service integration: the candidate is exercised through the runner's own HTTP API
     (`tests/service/runner/opencode.spec.ts`, `tests/service/harnesses/test_opencode_compatibility_service.py`,
     `tests/service/runner/mixed-harness-dispatch.spec.ts`).
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

## OpenCode 2

An OpenCode 2 binary runs a different proof. OpenCode 2 runs as a managed service the operator owns, and the proof
drives that service through its `/api/*` operations. These are the same operations the runner's OpenCode 2 driver core
uses. The isolated disposable data root and credential copy described above apply to OpenCode 1 only; OpenCode 2 uses
the operator's own service, login, and configuration.

### Before you run OpenCode 2

- Start the operator's service first (`opencode service start`). The proof finds it through the registration OpenCode
  writes under `XDG_STATE_HOME` (or `~/.local/state`) and authenticates with the password recorded there. The password
  never appears in output or evidence. With no service registered, the command fails before any row runs.
- The proof restarts the service once to prove a restart is recognized, and only when no other session is running. With
  any other session running, the `service_restart` row is reported `ambiguous`, so the run is blocking; rerun it with
  the service idle.
- The proof works in a disposable git repository under the system temporary directory. Every session it creates carries
  `blizzard` proof metadata, and it interrupts and removes each one on every exit path. It leaves the operator's own
  sessions untouched.
- To settle `store_migration`, have an OpenCode 1 binary and a model it can run. The row seeds a disposable 1.x store
  under an isolated home with that binary, lets OpenCode 2 open it, and has OpenCode 1 read it again. It never touches
  the operator's own store.

### Invocation for OpenCode 2

The required options are the same as for OpenCode 1. Two more options are optional:

- `--opencode1-binary PATH` — the OpenCode 1 executable that seeds the disposable store.
- `--opencode1-model TEXT` — the `provider/model` that OpenCode 1 seeds it with.

```bash
umask 077
blizzard runner opencode compatibility \
  --binary /path/to/opencode \
  --model openai/gpt-6-luna \
  --variant high \
  --evidence-dir /var/tmp/blizzard-opencode2-compatibility \
  --live-provider \
  --opencode1-binary /path/to/opencode1 \
  --opencode1-model opencode/pulse-alpha-free
```

Without both OpenCode 1 options, `store_migration` reports `ambiguous`, so the run is blocking.

### OpenCode 2 rows

The output has the same line shapes as the OpenCode 1 report: one line per row, then
`compatibility: supported|degraded|blocking`. The rows are:

- `version`, `managed_service` — `--version` matches the version the service reports, and `service start` returns and
  registers.
- `caller_named_session`, `session_environment`, `concurrent_sessions` — sessions keep a runner-minted id, rules, model,
  and metadata. A session's environment reaches its shell tools and no other session's, and sessions running at once
  share nothing.
- `prompt_and_completion`, `judgement`, `queue_and_steer`, `transcript_read` — prompts arrive verbatim, settling is
  observed by polling alone, the final text carries `<Choice>`, `queue` and `steer` deliveries behave, and messages read
  back with stable ids.
- `transcript_tools` — the read and skill tools run to completion and record their input, the message listing pages
  forward through its cursor to an empty last page, and a missing session answers `404`.
- `interrupt`, `service_restart` — an interrupt stops a tree, its running children included, and ends their tool
  processes. A restart is recognized by its changed registration `id` and `pid`, and any turn the restarted service
  resumes is interrupted.
- `usage_and_cost`, `pricing`, `errors_and_limits`, `model_and_variant`, `compaction` — usage is reported per session
  and per message, the model listing carries prices, unknown models and variants fail as `provider.no-route`, the model
  and variant hold, and compaction is recorded.
- `permission_rules`, `permission_requests` — deny rules hold, `ask` requests hold their turn until answered, a refusal
  sent with a message fails the call with it and the turn settles, and forms wait until they are cancelled.
- `events`, `child_agents`, `takeover` — every event carries its session id, child sessions link to their root, and
  `opencode --session <id>` continues a session.
- `store_migration` — OpenCode 2 marks a migrated 1.x store with the `kv` key `migration.v1-v2`, and OpenCode 1 can
  still read the store.

`observed` makes a row `supported`. An `absent` result is `degraded` only for `pricing` and `events`; every other result
is `blocking`. The evidence directory receives `report.json`, `manifest.json` (major version, each row's result, and the
declared degradations), and one sanitized fixture per capture. The OpenCode 1 store fixture goes under `opencode1/`.

**Pricing under a subscription login.** OpenCode lists no prices for a model covered by an OpenAI ChatGPT-plan login,
and lists zero prices for its own free models. So on such a login `pricing` is `absent` and the run is `degraded`. That
is a property of the login, not a defect in the binary.

### OpenCode 2 offline rehearsal

From a provisioned `blizzard-mock` checkout, emit an OpenCode 2 artifact: `uv run mock-opencode-2 emit --out <path>`.
Run it with an isolated `HOME` and `XDG_*` directories exported in the shell. Start its service with
`<path> service start`, then run the invocation above with `--binary` pointed at the emitted path. With the OpenCode 1
options pointing at a disposable OpenCode 1, every row is `supported`. Stop the service afterwards with
`<path> service stop`. A rehearsal proves the diagnostic runs end to end; it is not evidence about real OpenCode 2.
