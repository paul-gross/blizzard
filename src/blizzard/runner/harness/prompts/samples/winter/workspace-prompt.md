# Workspace policy

The preamble above is deployment-independent; this is the local law of a winter-shaped multi-worktree workspace. `<env>`
is your assigned feature-environment name — the `environment name` row of the facts table below. You spawn in the winter
workspace root; all work belongs inside your environment's worktrees, at the `environment workdir` row.

## Method

Your session executes exactly one node-step of a blizzard graph, and the node prompt is your charter: it defines the
step's scope, the assets it produces, and how your session ends. For the method inside that scope, prefer the
workspace's installed winter workflows, selected by the concepts the node prompt names; each workflow extension's
`index.md` routes to its processes, and when an extension declares a node-step → process map, follow it. Where the node
prompt's inline shorthand and the routed methodology differ in method, the methodology is current — the node prompt
still owns scope, assets, and endings. Do not use an end-to-end workflow that owns the whole plan → build → review →
deliver spine unless the node prompt explicitly directs it: the blizzard graph already is that process, and this node is
one step of it.

## Working in the environment

Your worktrees arrive on the base branch with no upstream. To land work on a feature branch, point the whole environment
at one with `winter ws connect <env> <feature-branch>`, and push it with `winter ws push <env>`.

If the task needs the environment running, start it with `winter service up <env> --wait`, then confirm readiness
against the app's own signal — a health endpoint, a startup log line — before verifying anything through it.

Keep your changes scoped to your assigned environment and to what the task asks. When the work is done, end your session
cleanly.
