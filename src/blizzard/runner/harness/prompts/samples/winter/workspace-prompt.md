# Workspace policy

The preamble above is deployment-independent; this is the local law of a winter-shaped multi-worktree workspace, and it
binds you alongside the machine-local facts table below.

Throughout, `<env>` is your assigned feature-environment name — the `environment name` row of the facts table. You spawn
in the winter workspace root, but all work belongs inside your environment's worktrees, at the path in the
`environment workdir` row.

## Method

Your session executes exactly one node-step of a blizzard graph, and the node prompt is your charter: it defines the
step's scope, the assets it produces, and how your session ends. For the method inside that scope, strongly prefer the
workspace's installed winter workflows, selected by the concepts the node prompt names — planning, plan review,
individual builds, verification, or whatever else an extension covers; the set is open-ended, and any extension
providing workflows around the node's concepts is fair to reach into. Each workflow extension's `index.md` routes to its
processes, and when an extension declares a map from node-steps to its processes, follow that map. If the node prompt's
inline method shorthand and the routed methodology differ in method, the methodology is current — the node prompt still
owns scope, assets, and endings. Do not use an end-to-end workflow that owns the whole plan → build → review → deliver
spine unless the node prompt explicitly directs it: the blizzard graph already is that process, and this node is one
step of it.

## Working in the environment

Your worktrees arrive on the base branch with no upstream. When your work has to land on a feature branch, point the
whole environment at one with `winter ws connect <env> <feature-branch>`, and push it with `winter ws push <env>`.

If the task needs the environment actually running, start it yourself with `winter service up <env> --wait`. That gate
is only as strong as the orchestrator's health probes, so confirm readiness against the app's own signal — a health
endpoint, a startup log line — before verifying anything through it.

Keep your changes scoped to your assigned environment and to what the task asks. This workspace's long-running commands
— a repo's task-runner gate, its test tiers, wheel builds, migrations — are exactly what the preamble's rule about work
still running at the end of a turn is for. When the work is done, end your session cleanly.
