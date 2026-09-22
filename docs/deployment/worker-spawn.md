# Worker spawn

## Harness identity

Every session is recorded and read under a harness id. A runner build binds two: `claude_code`, built once at startup
from `[worker]`'s `harness_binary` (and its sibling knobs below) in `blizzard-runner.toml`, and `opencode`, built from
its own `[opencode]` table ("OpenCode configuration" below) — each the same binary every spawn, judge, and resume child
for that harness runs. Which one a fresh mint actually spawns under is a per-`sessions:` entry declaration
(`harnesses:`/`default_harnesses`, "Acceptable harness set" below), never a runner-wide switch, so a deployment that
never names `opencode` anywhere never spawns it under that binding. Binding a harness does carry one small, bounded cost
regardless of whether anything ever spawns under it: each bound binary's version is probed and cached for the
fleet-registration push and the claim peek, refreshed at most once every ten minutes (long enough that an idle runner
pays it a few times an hour, not every ~30s tick; short enough that an in-place binary upgrade is noticed well within an
operator's own deploy window) — never once per tick, and never for a binary that isn't on `PATH` at all. A recorded
session's owner resolves against this runner's own bindings: **unknown** means the session was recorded under a harness
id this runner build doesn't ship at all — the remedy is to run a runner version that binds that id, on the runner
holding the chunk, never to substitute another harness. **unavailable** means the id is bound but this runner can't
supply the specific capability being asked of it — resuming or judging versus reading its transcript. Both `claude_code`
and `opencode` bind every capability the registry knows to ask for today, so this never fires for either; the loop's own
usage-recording and session-rotation call sites still treat an `unavailable` binding as their ordinary "no fallback
transcript" outcome, never a crash, for whichever future capability gap reintroduces one. Either shows up as an
`owner-unresolvable` event, which [observability.md](./observability.md) owns reading and resolving.

OpenCode's own plugin channel — a soft heartbeat nudge after every tool call, and forwarding the lease's identity into
tool subprocesses through `shell.env` — is classified `degraded` on every OpenCode version the runner admits today: the
compatibility proof's `root_hook` and `child_sessions` probes both report absent, so the plugin's loading at all is
never something a deployment can prove or depend on ([opencode-compatibility.md](./opencode-compatibility.md) owns the
full probe table and its classification policy). This is a fact of the committed corpora inside the admitted range, not
a blanket guarantee — the binding's declared degradations (`OpenCodeHealthProbe.declared_degradations`,
`src/blizzard/runner/harness/internal/opencode_health.py`) are the union across every in-range corpus manifest, so a
future corpus can change them; how an observed version resolves to its reference corpus is owned by
[opencode-compatibility.md § Admitting a candidate version](./opencode-compatibility.md#admitting-a-candidate-version).
Nothing about a turn's completion or a lease's correctness rests on it — process liveness is the sole signal for both
harnesses alike — so a runner whose OpenCode plugin never loads still executes work correctly and merely goes quiet
between tool calls.

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
name a dead lease); a fresh spawn, a node declared `session: fresh`, or a resume with no record of what the session was
last sent — nothing to safely elide against — renders all three layers in full. Layers 1 and 2 are standing prose, so a
node-step resuming an existing session sends each only when it changed since that session last spawned; unchanged, the
layer collapses to a single still-applies line — the ordinary case on advanced-development-workflow, whose worker nodes
resume by default, only plan-review and review being declared fresh. A changed standing layer is re-sent in full, led by
an explicit statement that the worker's standing instructions have been updated since its previous turn; a workspace
prompt replaced with an empty one is announced as a withdrawal. Whatever became of the layers, a resume whose recorded
prior node is known and differs from the current one carries a role-change line naming both nodes, first in the render;
with no recorded prior node there is nothing to name, and no line renders.

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

`[models.aliases]` and `[effort.aliases]` are optional for Claude Code: its adapter defaults `frontier` to fable,
`advanced` to opus, and `basic` to sonnet, so a zero-config runner resolves the tiers on that harness; an entry
overrides the built-in for that alias. `[effort.aliases]` maps onto the low|medium|high|max ordinal, which needs no
entries; the table names a deployment's own vocabulary or reaches a native tier outside the ordinal, such as Claude
Code's xhigh. OpenCode ships no built-in tier mapping at all, so its own `[opencode.models.aliases]` is not optional the
way Claude Code's is — an unmapped tier is a deliberately unavailable capability on that harness ("OpenCode
configuration" below), never a guessed native name. Nothing substitutes downward when a tier is unmapped on either
harness — aliases are roles, not a scale — so every degradation is authored.

The resolved tier vocabulary — the built-ins and whatever `[models.aliases]` overrides or adds — is also what the runner
advertises to the hub on registration, one binding per harness it can dispatch to: the hub never interprets a tier id,
but it does hold this runner's own list of them to match a chunk's requirements against later.

A model preference list resolves left to right: the first entry the runner can resolve wins, an unresolvable entry (an
unmapped alias, another harness's name) is skipped rather than failing the spawn. For a single acceptable harness — the
declared `harnesses:`/chunk `default_harnesses` set naming exactly one, or naming none at all — a fully unresolvable
list falls back to the runner's default model with a logged note naming what it skipped, exactly as today — but only
when nothing in that list is an authored (`blizzard:`-namespaced) tier. A single harness that cannot map an authored
tier in the list is skipped instead: such an entry was deliberately authored for *some* harness in the tier vocabulary,
never a "maybe this wasn't meant for me" native name, so silently substituting the runner's ambient default would spawn
under a capability nobody asked for.

A session's model is applied at mint and on no resume after, resting on the harness restoring a resumed session's own
model — a harness configuration that defeats that restore runs the lineage on the wrong model with every test tier still
green, so the constraints here are requirements, not preferences. Effort differs: Claude Code does not restore a
session's effort across `--resume` (it reverts to the settings-resolved default), so a mint-only effort would silently
drop on every member of a resuming pool — the runner therefore passes `--effort` on every invocation, at a small
measured cost.

## Acceptable harness set

A `sessions:` entry's `harnesses:` (and a chunk's `default_harnesses`) name the acceptable set a fresh mint may spawn
under, resolved harness-primary, model-inner: `HarnessSelector` walks the set in declared order, and for whichever
harness this runner can dispatch to, that harness's own model preference resolves as above — never the other way around,
so a later harness resolving an earlier-preferred model still loses to an earlier harness resolving a later one. With
two or more acceptable harnesses, resolution is strict: a harness resolving none of the model preference is skipped
entirely rather than falling back within it, so the single-harness fallback above holds only for that single-member case
— a multi-harness set instead moves on to the next member, and only escalates (`no-acceptable-harness`,
[observability.md](./observability.md)) once every member is exhausted. A retry keeps its prior lease's own owner when
that owner is still a member of the (possibly since-edited) acceptable set, and escalates the same way when it has
fallen out of one.

## OpenCode configuration

OpenCode's own knobs sit in `[opencode]`, parallel to `[worker]`'s Claude Code table rather than folded into it — the
two harnesses' bindings are independent, and no deployed `blizzard-runner.toml` needs an edit to keep working when this
table is absent (its scaffolded default binds `opencode` on `PATH`). `binary` names the OpenCode executable, exactly as
`harness_binary` does for Claude Code — and, exactly as for Claude Code, naming one that does not exist or is not
executable is caught by the runner's own health evaluation (blizzard#438) rather than surfacing only at spawn: health
recalculates at daemon start, after an operator-triggered selftest, and when the observed binary version changes, and a
binding that fails any required check — missing binary, an incompatible or unknown observed version, failed
authentication, an unmapped configured tier, or a recorded selftest failure — is marked unavailable. `HarnessSelector`
skips an unavailable member with its own `"unhealthy"` reason ([observability.md](./observability.md)) rather than
selecting it, so a node whose acceptable set includes `opencode` falls back to another member instead of failing at
spawn, and escalates only once every member is exhausted. The binding stays visible, with its cause, in this runner's
own `GET /api/harness-health` diagnostics; the hub sees only the boolean flag, never the cause.
`[opencode.models.aliases]` and `[opencode.effort.aliases]` mirror `[models.aliases]`/ `[effort.aliases]` in shape but
not in defaults — see "Model and effort tiers" above for why OpenCode's own table carries the whole mapping rather than
overrides to a built-in one. `worker_config_path` names the runner-owned permission/plugin document
`blizzard runner init` scaffolds beside `worker-settings.json` (never inside a project repository); it denies OpenCode's
native, non-interactive `question` tool outright — a headless worker has no one to answer it, and `blizzard runner ask`
is its lease-authenticated replacement — and names the heartbeat plugin ("Harness identity" above) for OpenCode to load.
Effort reasserts on every OpenCode invocation exactly as it does for Claude Code, for the same reason: a mint-only value
would silently drop across a resume.

## Compaction windows

A `sessions:` entry can carry an optional compaction window facet — one of five, alongside model, effort, rotation
bounds, and the harness set — an opaque string passed straight through to Claude Code's `--autocompact` flag on every
fleet-driven invocation (spawn, judge, resume-with-message). Whether a harness restores a resumed session's compaction
window is unmeasured, so the runner never bets on stickiness: it stamps the resolved window on the lease at mint and
reasserts it from that stamp on every resume, as it does effort; an unrecognized or empty value is dropped with one log
line, never failing a spawn. OpenCode has no comparable numeric threshold — its own compaction reserve and
automatic-compaction switch do not represent the same semantics — so the OpenCode binding always resolves this facet as
unsupported and omits it, the same one-log-line-and-drop treatment an unrecognized Claude Code value gets.

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
