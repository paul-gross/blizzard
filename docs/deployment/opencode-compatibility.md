# OpenCode compatibility

Use this procedure to run and interpret the runner's compatibility diagnostic for OpenCode `1.18.25`. It changes neither
runner or hub state nor the caller's checkout: the proof creates a temporary initialized git repository, may commit its
scratch work there, and removes that repository when the run ends. The runner may copy the account's normal OpenCode
credential file byte-for-byte into an isolated disposable data root after the version gate; OpenCode receives only that
isolated copy, and the runner never parses or retains credential values. This is diagnostic evidence for an external
seam, not production adapter selection or a claim that a provider is available to a fleet. A pass is established by the
command's observed output and retained evidence, not by this page. Version preflight uses empty isolated XDG data and
provisions disposable auth only after exact `1.18.25` matches.

## Before you run

Have these ready:

- A `blizzard` installation containing `runner opencode compatibility`.
- An OpenCode executable at an explicit path. The diagnostic observes its version and compares it with `1.18.25`.
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
- A version other than `1.18.25` makes the report `blocking`, regardless of probe results.

The final classification is `supported` when the pin matches and every probe is supported. It is `degraded` when the pin
matches and only the allowed neutral absences occur. Both classifications exit zero and are admissible under the policy;
`blocking` exits one and is not admissible. A complete report contains every required probe exactly once, and
`report.json` records `complete` and `admissible` for this check.

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
- the report is incomplete, the observed version is not `1.18.25`, or any probe is `blocking`.

Do not infer success from an individual OpenCode process exit or from a partial report. Use the final classification,
exit status, and the sanitized evidence together.
