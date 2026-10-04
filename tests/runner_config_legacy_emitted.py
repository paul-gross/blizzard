"""What ``RunnerConfig.to_toml`` emits for ``tests/test_config.py``'s legacy-shaped runner document —
the operator-facing TOML shape, pinned byte for byte."""

LEGACY_EMITTED = """# blizzard-runner runtime configuration (blizzard runner init)
db_url = "sqlite:///legacy.db"
host = "127.0.0.1"
port = 8431

# Reconciliation-loop seams.
hub_url = "http://127.0.0.1:8421"

# The browser-reachable origins this runner answers on — one bare origin, or a list.
# Empty registers no federation identity, so the human web surface stays unreachable.
# The browser follows these, so a loopback-only value answers on this host alone; a
# non-loopback origin must be https fronted by a proxy. First is canonical. See the
# "Runner-side federation" section of the deployment guide before changing this.
public_url = ""

# Reverse-proxy trust set: proxy IPs/CIDRs whose X-Forwarded-Proto is
# honored when minting the SSO session cookie's Secure flag. Empty = header ignored.
# Required for any https origin above, and the proxy must also pass the browser's
# original Host through — selection reads it, and nginx replaces it by default.
trusted_proxies = []

# Names the env var carrying this runner's hub bearer token;
# the secret itself lives in the runtime env file, never here.
token_env = "BZ_HUB_TOKEN"

# Names the env var carrying the secret that signs this runner's session cookie
# (base64, >= 32 bytes decoded; unique per runner). Unset = a fresh secret each start.
session_secret_env = "BZ_RUNNER_SESSION_SECRET"
runner_id = "runner-local"
workspace_id = "workspace-local"
workspace_root = ""
workspace_provider = "winter"
max_environments = 10
workspace_envs = ["e1"]
# Basic: add a [[workspace_repo]] for each git origin (name and url).
# Released folders remain for inspection until the cap needs room; oldest
# unheld folders are evicted first. Reacquisition resets all repo worktrees.
# A commented [[workspace_repo]] example is at the end of this file.
worker_settings_path = "/w/worker-settings.json"
claude_code_credentials_path = "/c/credentials.json"
max_agents = 1
base_branch = "main"

# Human gates this runner imposes by node name; empty = none.
gates = []

# The runner-owned workspace prompt prepended to a worker spawn.
# `workspace_prompt` is inline text; `workspace_prompt_file` (a path) wins when set.
# `workspace_prompt_package` names a sample shipped in the wheel and may not be
# combined with either (`blizzard runner prompt list` names them).
# Empty = table-only injection. Replace at runtime via PUT /api/workspace-prompt.
# A resumed spawn re-sends this only when it changed, announced as updated.
workspace_prompt = ""
workspace_prompt_file = ""
workspace_prompt_package = ""

# The operator's override of the baked-in blizzard preamble — layer 1
# of the spawn preamble, ahead of `workspace_prompt` above. `runner_prompt` is inline
# text; `runner_prompt_file` (a path) wins when set. Empty = the baked default
# (DEFAULT_BLIZZARD_PREAMBLE) is used instead; config/startup only, no runtime override.
runner_prompt = ""
runner_prompt_file = ""

# Where the coding harness writes session transcripts;
# empty = ~/.claude/projects.
transcripts_root = ""

# How freely an unattended worker may act without a human approving tool use:
# "normal", "auto", or "dangerous". Each harness translates it into its own terms.
[harness]
autonomy = "dangerous"
# An operator-owned bundle of harness configuration: optional `claude-code/` and
# `opencode/` directories, read at startup. Absolute path (~ allowed); absent = no bundle.
# config_dir = "~/.config/blizzard/harness"

# The transcript outbound lane — off by default; the hub's own
# durable, compressed-at-rest segment store is already landed, so
# turning this on is a rollout decision, not a bandwidth-for-nothing one.
[transcripts]
ship = false
# This lane's own byte ceilings, shown at their defaults;
# uncomment to override. Widen `chunk_max_bytes` for a backfill window — a
# `blizzard runner transcript reship` spends that budget a SECOND time over the
# same chunk — then restore it. Keep `record_max_bytes` at or BELOW the hub's own
# `record_max_bytes`: over the hub's, a record loses its turns whole; over this
# one, the pump merely shrinks them.
# record_max_bytes = 8388608
# chunk_max_bytes = 67108864

# This runner's selection over the peeked ready queue; off by
# default reaches past a marked head for the first unmarked entry. `true` holds
# at a marked head instead and idles rather than falling through.
[queue]
strict = false

# How long (days) a worker's captured stdout/stderr survive after being
# written, before the periodic sweep prunes them. A released lease's files are NOT
# deleted at release — only this age-based sweep removes them, both streams alike.
# Look up one invocation's own output at
# worker-stdout/<lease_id>.<generation>.{stdout,stderr}.
[worker_stdout]
retention_days = 14

# Trace knobs. Tracing turns on only through OpenTelemetry's own variables
# (OTEL_EXPORTER_OTLP_TRACES_ENDPOINT or OTEL_EXPORTER_OTLP_ENDPOINT); the
# sweep knobs tune fleet-trace export once it runs. Seconds, except batch_limit
# (closed leases per export). platform = true also emits platform spans (requests,
# queries, outbound calls), roots kept at platform_sample_ratio, 0 to 1.
# worker_programs = true (with platform) also lets a worker's own programs send
# spans to the runner; a third-party program may record bodies or parameters.
# [tracing.worker_program_services] names those spans' service.name by scope.
# Uncomment to override.
[tracing]
# sweep_seconds = 60
# settle_seconds = 300
# batch_limit = 200
# max_lag_seconds = 86400
# replay_max_window = 604800
# platform = false
# platform_sample_ratio = 0.01
# worker_programs = false

# [tracing.worker_program_services]
# winter_cli = "winter-blizzard"

# Spend controls (epic #57); absent = no cap. `chunk_cap_usd` parks a chunk
# needs_human at its next step boundary once its derived spend reaches this cap.
# `runner_ceiling_usd` engages this runner's own local pause brake (the same one
# `blizzard runner pause` sets) once its rolling `window_hours`-long spend reaches
# this value; `blizzard runner start` is the only clear — it does not lift itself
# when the window later rolls the spend back under the ceiling.
[cost]
# chunk_cap_usd = 5.0
# runner_ceiling_usd = 50.0
window_hours = 24.0

# The live session-context warn lane; absent = off, and nothing is sampled.
# `warn_tokens` is the context a RUNNING worker's session is warned about
# crossing — observation only, distinct from a graph's own `rotate` bounds,
# which decide whether the NEXT node-step resumes that session at all.
[context]
# warn_tokens = 300000
sample_interval_seconds = 60

# How often (seconds) the tick re-samples the harness's own subscription rate-limit
# windows — a diagnostic, best-effort read, not a spend control.
[external_subscription_usage]
sample_interval_seconds = 300
# credentials_path = "/path/to/.credentials.json"  # defaults to ~/.claude/.credentials.json

# Declared provider subscriptions — the join key everything
# downstream keys on is `slug`, runner-unique and immutable once observed. Absent
# entirely (the default): the `[external_subscription_usage]` table above is the
# sole subscription, synthesized under the reserved slug "anthropic". Any
# `[[subscription]]` present here instead takes over completely — the legacy table
# is no longer consulted for the runtime list.

# The worker spawn-environment allowlist's operator extension (`bzh:worker-env-allowlist`).
# The base allowlist (PATH/HOME/USER/LANG/LC_*/TERM/TMPDIR) always reaches a worker;
# name additional vars here to forward them too. Empty = base allowlist only. The
# BLIZZARD_* identity vars are injected per spawn/judge/resume, not passed through.
[worker]
env_passthrough = []
# Absolute directories (~ allowed) led onto every worker's PATH, ahead of the
# daemon's own — e.g. a mise shims dir, so a spawned worker resolves the same
# version-manager tools an operator's shell does. A relative entry fails config
# load. `runner host` warns at startup for any entry missing on disk, but still
# starts. Empty = the daemon's own PATH, unchanged.
path_prepend = []

# Runner-local role resolution, keyed by hub username — lives only here,
# never in the hub store/admin page. `hub_role_default` is "mirror" or a fixed cap
# ("contributor"/"guest"/"pending"); `superuser` names this runner's own sovereign.
[auth]
# superuser = "<hub-username>"
hub_role_default = "mirror"

[auth.users]

# The Claude Code binding: `enabled = false` leaves it unbound and unadvertised.
[claude_code]
enabled = true
binary = "/opt/claude"

# Model and effort tier aliases — how THIS runner's harness resolves the
# harness-agnostic names a graph's `sessions:` declaration (or a chunk default) uses.
# The Claude Code adapter ships built-in defaults for the three standard tiers
# (blizzard:frontier/advanced/basic), so a zero-config runner needs no entry here;
# an entry overrides the built-in. An unmapped alias is skipped at resolution, never
# a spawn failure. Effort maps onto the low|medium|high|max ordinal.
[models.aliases]
"blizzard:frontier" = "opus"

[effort.aliases]
"blizzard:deep" = "high"

# The OpenCode binding's own configuration — fully independent of the flat
# Claude Code fields above, which keep their existing meaning unchanged. OpenCode
# ships no built-in tier mapping, so an unmapped tier makes this binding unable to
# satisfy a session demanding it; a multi-harness selection skips it rather than
# spawn it under a model it cannot provide.
[opencode]
enabled = false
binary = "/opt/opencode"
worker_config_path = "/w/opencode-worker-config.json"
auth_path = "/a/auth.json"

[opencode.models.aliases]
"blizzard:frontier" = "openai/gpt"

[opencode.effort.aliases]
"high" = "max"

# [[workspace_repo]]
# name = "my-repo"
# url = "https://github.com/you/my-repo.git"
"""
