# Artifacts

## Graph-scope artifacts

A graph's top-level `artifacts:` map — a sibling of `nodes:` and `sessions:` — declares reference content the graph
itself carries, readable by every node of every chunk on it; each value is a path to a file beside `graph.yaml`. An
`artifacts:` file's text folds into the definition at mint like a prompt reference — [install.md](./install.md)'s graph
sync note owns the deploy consequence, `blizzard hub graph mint --help` the inlining rules.

The folding rules differ from prompts, and the difference decides authoring: a prompt value inlines only when it reads
as a path (literal prose stays literal), but every `artifacts:` value is read as a filename — inline text in a
disk-loaded `graph.yaml` fails the load naming the entry. Inline artifact text is authorable only where no directory
exists to resolve against — a definition piped through `blizzard hub graph mint -` or posted to `POST /api/graphs`;
there a value that still reads as a file path (a whitespace-free token with a slash or extension) is rejected as a
validation error rather than baked in — though a bare extension-less token like `notes` knowingly mints as content.

Because content is baked at mint, editing the referenced file changes nothing for running chunks — they stay on their
mint until `graph sync` mints a new one. Authored order is fixed at the mint that first carried an entry: a pure reorder
of `artifacts:` (or `sessions:`) entries is not a definitional difference, so `graph sync` reports up-to-date while
workers keep the original order; to move an entry, pair the reorder with a substantive edit.
`blizzard hub graph show <graph_id> --json` lists a mint's artifact names in their baked order; the default human
rendering shows nodes and edges only.

The graph scope is read-only to workers: `artifact get <name> --scope graph` reads an entry (`--content` for raw text)
and `artifact list` shows graph entries beside the node's own; `create`, `commit`, and `staged` refuse `--scope graph`.
A name colliding with any node's `produces:` name is rejected: workers reach both scopes through one `artifact` CLI, so
a shared name would be ambiguous rather than a legal shadow.

## System-scope artifacts

Blizzard itself publishes a small, global set of read-only documents — a slash-bearing namespace of its own, not a
per-graph one — that every graph and every chunk reads the identical copy of; no graph declares it, and no worker ever
produces it. `garden/finding-format` and `garden/proposal-format` are the shipped examples: the shapes a garden
routine's finding and proposal artifacts are meant to conform to, held in lockstep with the `blizzard.wire.finding`
and `blizzard.wire.garden_proposal` models by a dedicated test rather than generated from them.

A system-scope read is always a live call to the hub, on every invocation, unlike a graph-scope read: `artifact get
<name> --scope system` and `artifact list --scope system` never answer from a runner-local pin or cache, so if the hub
is unreachable when a worker makes the call, the read fails outright rather than answering from a stale or absent
local copy.

The read-only rule matches graph scope: `create`, `commit`, and `staged` all refuse `--scope system`. `artifact list`
and `artifact get --scope system` otherwise serve system scope much the way they serve graph scope — `get` resolves
one artifact by name (`--content` for raw text), and `list` includes it in the unfiltered read alongside node and
graph scope — with one difference: a system name colliding with a node's `produces:` name is not rejected at mint the
way a graph-scope collision is, since blizzard's own global namespace and a deployment's per-graph declarations are
authored by different parties with no shared mint to reject at. The collision surfaces instead at read: a bare
`artifact get <name>` matching both a node output and a system artifact is a `409` naming both, resolved by adding
`--node` (which only a node-scoped candidate has) or `--scope`.

## Declaring produced artifacts

Each `produces:` entry carries a kind: a bare string is an asset, while a `{name, kind: git_commit}` entry is met by
kind rather than name — any `git_commit` artifact on the node's attempt covers it.

Workers declare through `blizzard runner artifact`: `create --name` with content on stdin for an asset;
`commit --repo --branch --commit`, after the push, for a `git_commit` — the origin comes from the environment's repo
manifest, not a flag. A `git_commit` entry is met only when the worker pushed its branch to the forge and then declared
the push — the worker pushes, never the runner, an undeclared push does not count, and a name backed only by the
assessment fallback is not proof the thing was produced.

The artifact verbs are pure clients of the runner's local API, authorized by the spawn-injected lease identity;
[openapi/runner.openapi.json](../../openapi/runner.openapi.json) owns the endpoint shapes.

An artifact name is alphanumerics with internal `-`, `_`, or `.` separators — no leading, trailing, or doubled
separator, and no slash, since it is percent-encoded into a URL path segment.

## The `produces_mode` rollout flag

`produces_mode` is a third warn-default rollout flag scaffolded into `blizzard-hub.toml` by `hub init`, beside
`runner_auth_mode` and `route_token_mode` ([runner-auth.md](./runner-auth.md)), guarding whether every `produces:` entry
on a node has an explicit declaration matching its kind. `warn` logs the missing names and lets a completion proceed on
the judgement-assessment fallback; `enforce` rejects the completion as a semantic failure. It is independent of the auth
flags and no part of that rollout; a hub accepts assessment-fallback completions until an operator sets `enforce` and
restarts it.
