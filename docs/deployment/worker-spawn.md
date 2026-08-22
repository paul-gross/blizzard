# Worker spawn — forwarded vars, tiers, and the preamble

## Forwarding extra vars to workers

`blizzard-runner.toml`'s `[worker] env_passthrough` is the operator's lever to widen the fixed base allowlist
(`PATH`/`HOME`/`USER`/`LANG`/`LC_*`/`TERM`/`TMPDIR`) every worker/judge/resume child process is built from — name a
variable there to forward it into every spawn too. Empty (the fresh-scaffold default) means the base allowlist only; a
daemon credential such as `BZ_HUB_TOKEN` is never in scope for this list, so it is absent from a worker child by
construction unless deliberately named here.

**One child is built the other way around: an operator takeover.** A session continued via `blizzard runner takeover`
runs in *your terminal's* environment — your shell as the base — with only a bounded daemon-side set layered on top: the
lease's `BLIZZARD_*` identity vars plus the daemon's `PATH` and `HOME`. Nothing named in `env_passthrough` is forwarded
to it, and no allowlist filtering applies to your own shell. See "Taking over a parked session" under the control verbs.

## Model and effort tiers

A graph's `sessions:` map names each session lineage's **capability tier** rather than a model — `blizzard:frontier`,
`blizzard:advanced`, `blizzard:basic` — and a chunk's `default_model` uses the same vocabulary. The hub never interprets
either: the mapping from a tier to a model *this* runner's harness understands lives in `blizzard-runner.toml`, which is
what keeps a graph harness-agnostic. A runner on a second harness would map the same three tiers to that harness's own
models and skip `opus` wherever a preference list names it — Claude Code is the only adapter that ships today.

```toml
[models.aliases]
"blizzard:advanced" = "claude-opus-5"
"blizzard:basic" = "haiku"

[effort.aliases]
max = "xhigh"
```

Both tables are optional. The Claude Code adapter ships built-in defaults for the three standard tiers (frontier →
`fable`, advanced → `opus`, basic → `sonnet`), so a zero-config runner resolves them with no `[models.aliases]` at all;
an entry here overrides the built-in for that alias. `[effort.aliases]` maps onto the well-known `low|medium|high|max`
ordinal — the four need no entry, and the table exists so a deployment can name its own vocabulary or reach a native
tier outside the ordinal (Claude Code's own `xhigh`).

A `model` preference list resolves **left to right**: the first entry this runner can resolve wins, and an entry it
cannot — an unmapped alias, or a name belonging to another harness — is **skipped**, never a spawn failure. A list
nothing in resolves falls back to the runner's own default model with a logged note naming what it skipped. The aliases
are deliberately **unordered roles, not a scale**: nothing substitutes downward when a tier is unmapped, so every
degradation is something a graph author wrote.

### Session stickiness — a deployment requirement

A session's **model** is applied when the session is minted and on no resume after it. That rests on the harness
restoring a resumed session's own model, which all three target harnesses do — and which each one has a configuration
that **defeats**. A deployment that trips one runs its mechanical lineage on the wrong model with every test tier still
green, so these are requirements, not preferences.

Only the first binds a deployment you can run today; Claude Code is the one adapter that ships, and the other two are
the obligation an adapter for that harness would inherit:

- **Claude Code** — a worker must never see the `ANTHROPIC_MODEL` family of variables. They are absent from the base
  allowlist by construction; do not add one through `[worker] env_passthrough`. The by-construction guarantee covers
  **daemon-spawned** children only: a `blizzard runner takeover` session inherits your shell, so a shell that exports
  `ANTHROPIC_MODEL` moves that one session off its sticky model — unset it before taking over.
- **opencode** — an adapter must not pin `agent.<name>.model`; it outranks session stickiness.
- **codex** — an adapter must keep `model` out of `config.toml` (it overrides every resume), and needs a state-DB-era
  codex to restore a thread's model at all.

**Effort is different, and is reasserted on every invocation.** Claude Code does *not* restore a session's effort across
`--resume`: a session spawned at one effort reverts to the settings-resolved default on the next resume (measured
against CLI 2.1.220). Applying it at mint only would therefore silently drop a declared effort on every member of a
resuming pool, so the runner passes `--effort` on each turn. The cost is small and measured — 249 cache-creation tokens
against 17 for a bare resume, nothing like the full-history rewrite a cross-model resume forces.

**A declared compaction window is treated the same way as effort (blizzard#343): reasserted, never mint-only.** A
`sessions:` entry can carry a fourth, optional facet — a compaction window, an opaque string passed straight through to
Claude Code's `--autocompact <auto|tokens>` flag on every fleet-driven invocation (spawn, judge, resume-with-message).
Whether the harness restores a resumed session's own window is unmeasured, so the runner does not bet on stickiness the
way it does for `model` — it stamps the resolved window on the lease at mint and reasserts it from that stamp on every
resume, exactly as it does for effort. An unrecognized or empty value is dropped with one log line rather than failing a
spawn. `advanced-development-workflow` declares one on all four of its pools at the same value, above the
`rotate.max_context_tokens` its bounded pools carry (the only one of those three rotation bounds a window is
commensurable with). The ordering is the whole decision: set below that bound, a window fires first and keeps firing
inside one long node, costing the worker its working context on every firing; set above it, rotation ends an ordinary
lineage first and the window is left as a ceiling on the one invocation that outgrows it before the next resume is
measured. Neither number is fit from measured compaction behavior the way the rotation bounds are, since no such data
exists yet.

## The worker spawn preamble

A worker's **first** spawn on a session carries three ordered layers ahead of the node's own envelope prompt: (1) a
baked-in blizzard preamble — framing the worker as operating inside the fleet, naming its worker-facing
`blizzard runner` verbs (`ask`, `work-items`), and stating the turn-ending discipline a headless session is held to
(nothing survives the turn that started it) — (2) the operator's own `workspace_prompt` prose, layered on top when set,
and (3) a machine-local facts table (runner/chunk/lease identity, held environment(s)). Layer 1 closes by stating that
division of labor for the worker's own benefit; read the shipped text
(`src/blizzard/runner/harness/prompts/blizzard_preamble.md`) before authoring layer 2, so your prose adds
deployment-specific policy rather than re-establishing framing the worker already has.

### Adopting a packaged sample instead of authoring layer 2

Blizzard ships a corpus of workspace prompts — one per deployment shape — so a workspace whose shape is already
represented names one rather than writing layer 2 from scratch. `blizzard runner prompt list` names what this wheel
carries, and `blizzard runner prompt show <name>` prints one. A sample is never applied by default; it takes a knob:

```toml
workspace_prompt_package = "winter"
```

That knob resolves the named sample out of the installed wheel at `host` startup, so nothing lands in the runtime root
and a redeploy carrying a changed sample applies it on the next restart. It is **exclusive** with `workspace_prompt` and
`workspace_prompt_file` — those two keep their own file-wins-over-inline precedence, and setting the package knob
alongside either fails startup rather than ranking them. A name the corpus does not carry fails startup too, listing
what it does carry.

To fork a sample instead of tracking it, `blizzard runner prompt install <name>` copies it into the runtime root and
sets `workspace_prompt_file` at the copy — never the package knob, so `blizzard runner prompt diff <name>` always has a
local file to compare and can report drift from the sample it came from. `blizzard runner prompt status` reports which
source the effective prompt resolves from, and exits non-zero when a source is configured but resolves to nothing.

### What the packaged graphs delegate to layer 2

`workspace_prompt` is unset by default, and the packaged graphs still work without it — their prompts state each duty as
an outcome a worker can satisfy on its own. But two of those duties are ones a workspace usually has a specific, better
answer for, and the prompts defer to it by name ("if this workspace declares one, prefer that"). Authoring layer 2 is
how you supply that answer:

- **Getting onto the feature branch.** The build prompts require that no push from a leased environment can reach the
  base branch, and leave *how* to the workspace. If your workspace has a command that points every repo's upstream at
  the feature branch in one step, name it — a worker doing this per-repo by hand is the slower, more error-prone path,
  not a different outcome.
- **Where scratch files go.** The prompts require drafts to land outside every repository working tree and outside the
  workspace directory the worker was spawned in, and fall back to a per-chunk directory in the machine's temporary
  space. If your workspace owns a scratch area that something actually sweeps, name it.

Neither is a safety gap when layer 2 is absent — the prompts are self-sufficient — but a deployment that has better
answers and does not state them leaves workers taking the generic path.

### What a resumed spawn gets instead

Layers 1 and 2 are *standing* prose — a session that already received them still holds them. So on a node-step that
**resumes** an existing session, the runner sends each of those two layers only when it has actually changed since that
session was last spawned:

- **Unchanged** — the layer collapses to a single line stating that it still applies. On a graph like
  `advanced-development-workflow`, whose worker nodes resume by default (`plan`, `build`, `verify`, `pre-push`,
  `resolve`, `retrospective` — only `plan-review` and `review` are declared `fresh`), that is the ordinary case at every
  one of them.
- **Changed** — the new prose is sent in full, led by an explicit statement that the worker's standing instructions have
  been updated since its previous turn. A workspace prompt replaced with an empty one is a change too, and is announced
  as a withdrawal.

That announcement is the operator-visible reason `PUT /api/workspace-prompt` is trustworthy mid-chunk: a replace applies
to the chunk's next resumed node-step, and the worker is told it is reading something new rather than being handed
replacement prose in the same position the superseded block occupied. `runner_prompt` behaves the same way once it
moves, but it is a startup knob — reaching a running fleet still takes the runner restart described below.

An override is a standing one: it wins over every config knob until it is removed, and replacing it with empty text sets
a standing *empty* prompt rather than restoring the configured one. `DELETE /api/workspace-prompt` is the way back — it
drops the override so the config resolves again, which is what makes the override usable as a live scratchpad for prose
you intend to land in the corpus.

**Layer 3 is unconditional on every path.** The facts table is re-rendered per attempt around a freshly minted
`lease_id`, and a worker whose table named a dead lease could not address the fleet at all. A fresh spawn, and any node
declared `session: fresh`, renders all three layers exactly as before.

Layer 1 is overridable but never *unset* — some layer-1 prose is always in effect, even on a resumed spawn that only
restates it in one line. `blizzard-runner.toml`'s `runner_prompt` (inline text) or `runner_prompt_file` (a path, wins
over inline text when both are set) — or `BZ_RUNNER_PROMPT` seeding a fresh scaffold — replaces the baked default
wholesale when set; unset, the baked default renders. Both are config/startup knobs, resolved once at `host` startup —
unlike `workspace_prompt`, which also has a live `PUT /api/workspace-prompt` override, `runner_prompt` has no runtime
door, so changing it means restarting the runner. A `runner_prompt_file` naming a path that does not exist raises a
`ConfigError` at startup, the same fail-fast the workspace-prompt file knob already gives.
