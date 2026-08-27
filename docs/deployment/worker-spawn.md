# Worker spawn

## The three prompt layers

A worker's first spawn on a session carries three ordered layers ahead of the node's own envelope prompt: a baked-in
blizzard preamble, the operator's `workspace_prompt` prose when set, and a machine-local facts table (runner, chunk, and
lease identity, plus held environments).

The baked preamble frames the worker as operating inside the fleet, tables its worker-facing `blizzard runner` verbs,
and states the turn-ending discipline of a headless session — nothing survives the turn that started it. Read the
shipped preamble text
([src/blizzard/runner/harness/prompts/blizzard_preamble.md](../../src/blizzard/runner/harness/prompts/blizzard_preamble.md))
before authoring workspace prose, so it adds deployment-specific policy rather than re-establishing framing the worker
already has.

Layer 1 is overridable but never unset — some layer-1 prose is always in effect: `runner_prompt` (inline text) or
`runner_prompt_file` (a path, winning when both are set), or `BZ_RUNNER_PROMPT` seeding a fresh scaffold, replaces the
baked default wholesale; unset, the baked default renders. `runner_prompt` resolves once at host startup with no runtime
door — unlike `workspace_prompt`'s live PUT — so changing it means restarting the runner; a `runner_prompt_file` naming
a missing path raises a `ConfigError` at startup, the same fail-fast as the workspace file knob.

Layer 3 is unconditional on every path, re-rendered per attempt around the freshly minted lease_id (a stale table would
name a dead lease); a fresh spawn, or a node declared `session: fresh`, renders all three layers in full. Layers 1 and 2
are standing prose, so a node-step resuming an existing session sends each only when it changed since that session last
spawned; unchanged, the layer collapses to a single still-applies line — the ordinary case on
advanced-development-workflow, whose worker nodes resume by default, only plan-review and review being declared fresh. A
changed standing layer is re-sent in full, led by an explicit statement that the worker's standing instructions have
been updated since its previous turn; a workspace prompt replaced with an empty one is announced as a withdrawal.
Whatever became of the layers, any resume whose node differs from the one that produced the session's previous turn
carries a role-change line naming both nodes — first in the render, except under the update announcement when one leads
it.

That announcement is why `PUT /api/workspace-prompt` is trustworthy mid-chunk: a replace applies at the chunk's next
resumed node-step, and the worker is told it is reading something new. The workspace-prompt override is standing: it
wins over every config knob until removed, and replacing it with empty text sets a standing empty prompt rather than
restoring the configured one; `DELETE /api/workspace-prompt` drops the override so config resolves again.

## Workspace prompt sources

`workspace_prompt` is unset by default and the packaged graphs work without it, but their prompts defer two duties to
the workspace by name: getting onto the feature branch (no push from a leased environment may reach the base branch;
name your one-step command if you have one) and scratch-file placement (outside every repo tree and the spawn workspace,
defaulting to a per-chunk temp directory; name your swept scratch area if you own one); absent prose is no safety gap,
but a deployment with better answers that keeps them quiet leaves workers on the generic path.

Blizzard ships a corpus of workspace prompts, one per deployment shape; `blizzard runner prompt list` names what the
installed wheel carries and `prompt show` prints one — none is ever applied by default.
`workspace_prompt_package = "<name>"` resolves the named sample out of the installed wheel at host startup, so nothing
lands in the runtime root and a redeploy carrying a changed sample applies it on the next restart. The package knob is
exclusive with `workspace_prompt` and `workspace_prompt_file` (which keep their file-wins-over-inline precedence):
setting it alongside either fails startup rather than ranking them, and a name the corpus does not carry fails startup
listing what it does.

`blizzard runner prompt install` copies a sample into the runtime root and sets `workspace_prompt_file` at the copy —
never the package knob — so `prompt diff` always has a local file and can report drift from the sample it came from.
`prompt status` reports which source the effective prompt resolves from, exiting non-zero when a source is configured
but resolves to nothing.

## Model and effort tiers

A graph's `sessions:` map names each session lineage's capability tier — `blizzard:frontier`, `blizzard:advanced`,
`blizzard:basic` — and a chunk's `default_model` uses the same vocabulary; the hub never interprets either, because the
tier-to-model mapping lives in each runner's `blizzard-runner.toml`, keeping graphs harness-agnostic.

`[models.aliases]` and `[effort.aliases]` are optional; the Claude Code adapter — the only one shipping — defaults
`frontier` to fable, `advanced` to opus, and `basic` to sonnet, so a zero-config runner resolves the tiers; an entry
overrides the built-in for that alias. `[effort.aliases]` maps onto the low|medium|high|max ordinal, which needs no
entries; the table names a deployment's own vocabulary or reaches a native tier outside the ordinal, such as Claude
Code's xhigh. Nothing substitutes downward when a tier is unmapped — aliases are roles, not a scale — so every
degradation is authored.

A model preference list resolves left to right: the first entry the runner can resolve wins, an unresolvable entry (an
unmapped alias, another harness's name) is skipped rather than failing the spawn, and a fully unresolvable list falls
back to the runner's default model with a logged note naming what it skipped.

A session's model is applied at mint and on no resume after, resting on the harness restoring a resumed session's own
model — a harness configuration that defeats that restore runs the lineage on the wrong model with every test tier still
green, so the constraints here are requirements, not preferences. Effort differs: Claude Code does not restore a
session's effort across `--resume` (it reverts to the settings-resolved default), so a mint-only effort would silently
drop on every member of a resuming pool — the runner therefore passes `--effort` on every invocation, at a small
measured cost.

## Compaction windows

A `sessions:` entry can carry an optional fourth facet, a compaction window: an opaque string passed straight through to
Claude Code's `--autocompact` flag on every fleet-driven invocation (spawn, judge, resume-with-message). Whether a
harness restores a resumed session's compaction window is unmeasured, so the runner never bets on stickiness: it stamps
the resolved window on the lease at mint and reasserts it from that stamp on every resume, as it does effort; an
unrecognized or empty value is dropped with one log line, never failing a spawn.

The window-versus-rotation ordering is the whole authoring decision: set below `rotate.max_context_tokens` — the only
rotation bound a window is commensurable with — the window fires repeatedly inside one long node, costing the worker its
working context each firing; set above it, rotation ends an ordinary lineage first and the window remains a ceiling on
the one invocation that outgrows it. advanced-development-workflow sets one window on all four pools, above that bound;
neither number comes from measured compaction data — none exists yet.

## The worker environment

`[worker]` `env_passthrough` in `blizzard-runner.toml` widens the fixed base allowlist (`PATH`, `HOME`, `USER`, `LANG`,
`LC_*`, `TERM`, `TMPDIR`) every worker, judge, and resume child environment is built from; empty (the scaffold default)
means base allowlist only, and a daemon credential such as `BZ_HUB_TOKEN` is absent from every worker child by
construction unless deliberately named there.

For Claude Code a worker must never see the `ANTHROPIC_MODEL` family: absent from the base allowlist by construction,
never to be added through `env_passthrough`; the guarantee covers daemon-spawned children only — a shell exporting
`ANTHROPIC_MODEL` moves a takeover session off its sticky model, so unset it before taking over.

An operator takeover session inverts this: your shell is the base with only a bounded daemon-side set on top — the
lease's `BLIZZARD_*` identity vars plus the daemon's `PATH` and `HOME`; `env_passthrough` is not forwarded and no
allowlist filters your shell ([chunk-operations/takeover.md](./chunk-operations/takeover.md) owns the verb).
