# Artifacts — produces enforcement and graph-scoped files

## Produces-artifact enforcement

`produces_mode` is a third rollout flag, scaffolded into `blizzard-hub.toml` by `blizzard hub init` alongside
[`runner_auth_mode`/`route_token_mode`](./runner-auth.md) and defaulting to `warn` the same way — but it guards a
different concern: not runner identity or route capability, a node's own `produces:` declaration. Each `produces:` entry
carries a **kind**: a bare string (`review-findings`) is an `asset`; a `{name, kind: git_commit}` entry (a build node's
own commit) is met by kind, not by name — any `git_commit` artifact the node's attempt carries covers it. A `git_commit`
entry is met when the worker has **pushed** its branch to the forge and then **declared** that push — the worker pushes,
never the runner, and an undeclared push does not count. A name backed only by the worker's judgement-assessment
fallback is not proof the worker produced the thing the graph asked for.

| Flag            | Guards                                                                                                                    | `warn` (default)                                                                  | `enforce`                                    |
| --------------- | ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | -------------------------------------------- |
| `produces_mode` | every `produces:` entry has an explicit declaration matching its kind (an asset attachment by name, a git commit by kind) | logs the missing names and lets the completion proceed on the assessment fallback | rejects the completion as a semantic failure |

The worker declares each kind through its own `blizzard runner artifact` verb: `artifact
create --name <name>` (content
on stdin) for an `asset`; `artifact commit --repo <repo>
--branch <branch> --commit <sha>` for a `git_commit`, run after
the branch is pushed. The origin is not a flag on that verb: it comes from the environment's repo manifest. Both verbs
are pure clients of the runner's local API, authorized by the lease identity the runner injects at spawn — see
[`openapi/runner.openapi.json`](../../openapi/runner.openapi.json) for the endpoints
(`POST /api/leases/{lease_id}/attachments` and `POST /api/leases/{lease_id}/git-commits`) rather than this doc
hard-copying their request shape.

It is independent of `runner_auth_mode`/`route_token_mode` — flipping it does not depend on either of them, and vice
versa — so it is not part of the runner-auth rollout sequence. A fresh deploy or an upgraded hub keeps accepting
assessment-fallback completions until an operator deliberately flips it to `enforce` in `blizzard-hub.toml` and restarts
the hub.

## Graph-scoped artifacts

Where `produces:` declares what a *node* must hand back, a graph's top-level `artifacts:` map declares content the
**graph itself** carries — reference material every node of every chunk on that graph can read, authored once beside the
definition. It is a sibling of `nodes:` and `sessions:`, and each value is a path to a file next to `graph.yaml`:

```yaml
artifacts:
  docket: ./docket.md
```

The file's text is folded into the definition at mint, the way a `prompt` reference is — see the `graph sync` paragraph
under [Install](./install.md) for what that means for a deploy, and `blizzard hub graph mint --help` for the inlining
rules themselves. A `prompt` and an `artifacts:` value are not folded by the same rule, though, and the difference
decides how you author: a `prompt` value is inlined only when it *reads* as a path, so literal prompt prose stays
literal, while **every** `artifacts:` value is read as a filename. In a `graph.yaml` loaded from disk, an artifact value
carrying inline text is therefore not accepted at all — the loader tries to open a file named by that text, and fails
the load naming the entry.

That leaves inline text authorable on exactly one path: a definition arriving with **no directory to resolve against**,
piped through `blizzard hub graph mint -` or posted straight to `POST /api/graphs`. Nothing is inlined there, so the
text has to arrive in the definition itself — and a value that still reads as a file path is rejected as a validation
error rather than baked in as the artifact's content. That guard fires on a single whitespace-free token carrying either
a `/` or a filename extension; real content is prose, which carries whitespace, so it cannot collide with it. One shape
slips through knowingly: a bare extension-less token like `notes` is as plausible a one-word artifact as it is a
filename, and mints as content.

And because the content is baked, editing the referenced file changes nothing for the chunks already running: they stay
on the mint they started under until a `graph sync` mints a new one.

Each name must be alphanumerics with internal `-`, `_`, or `.` separators — non-empty, no leading or trailing separator,
no two separators in a row (`a--b` and `a._b` are both rejected), and no `/`, since the name is percent-encoded into a
URL path segment on the way to a worker. A name that collides with any node's `produces:` name is rejected too: a worker
reaches both scopes through the one artifact CLI, so a shared name would be genuinely ambiguous rather than a legal
shadow.

**The graph scope is read-only to workers.** A worker reads an entry with
`blizzard runner artifact get <name> --scope graph` (add `--content` for the raw text), and `artifact list` includes the
graph's entries alongside the node's own. The writing verbs refuse the scope outright — `artifact create`,
`artifact commit`, and `artifact staged` all reject `--scope graph`, since a mint-time declaration is not something an
attempt produces.

**Authored order is fixed at the mint that first carried an entry.** Reconciliation mints only when a packaged graph's
parsed definition differs, and reordering two `artifacts:` entries without changing either name or either file is not
such a difference — `graph sync` reports the graph `up-to-date` while workers keep seeing the original order. The same
holds for `sessions:`. To move an entry, pair the reorder with a substantive edit to the graph or one of its referenced
files. To read back what a mint actually carries, `blizzard hub graph show <graph_id> --json` lists the artifact names
in their baked order — the default human rendering is nodes and edges only, so `--json` is the one that shows them.
