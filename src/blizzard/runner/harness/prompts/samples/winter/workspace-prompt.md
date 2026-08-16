# Workspace policy — this winter deployment

What follows is specific to how work is done in *this* workspace. Your fleet identity and worker CLI surface are
established above; the machine-local facts table naming your runner, chunk, lease, and assigned environment(s) follows
**below** this section.

Below, `<env>` means your assigned feature-environment name — the `environment name` row of that table (also
`BLIZZARD_ENV_IDS` / `BLIZZARD_ENV_WORKDIRS` in your environment). Your current directory is the winter **workspace
root**; do all of your work inside your assigned environment's worktrees, at the `environment workdir` row.

## Before a fresh build: reset to a known-good baseline

If you are starting a **new build** — not resuming in-progress work in an environment you already set up — return the
environment to a clean baseline first, **in this order**:

1. `winter service down <env>`

2. `winter ws fetch --all`

   Refresh remote-tracking refs first — `winter ws checkout` is a no-network command, so without a fresh fetch the reset
   lands on whatever the base branch was cached as in the worktree, and you build against a stale base.

3. `winter ws checkout <env> {main} && winter ws disconnect <env>`

   Reset every worktree to the base branch **before** provisioning. Dependency handlers run inside the worktree, so they
   must install against the code you are about to build, not the previous build's. `{main}` is a ref token that expands
   to each repo's *own* main branch, so this resets correctly in a workspace whose repos disagree on the name. The
   `disconnect` drops the checkout's base-branch upstream link immediately, so a later `git push -u origin <branch>`
   targets that branch — never the base branch.

4. `winter provision <env> --stage resource --destroy`

5. `winter provision <env>`, then `winter service up <env>` — only if your task needs the environment actually running.
   **Never exercise an environment you have not provisioned.**

For what any of these commands do, read `workspace:/context/winter-cli/usage/provision.md` and
`workspace:/context/winter-cli/usage/ws/checkout.md`.

**If step 3 fails for any reason, stop immediately.** Do not hand-repair worktrees, do not add `--force`, and do not
begin the task on an unknown baseline. Report the failure and end your session — a broken baseline is the fleet's
problem to resolve, not yours.

## Work through the workspace's methodology, at your node's scale

Your session executes **one node-step** of a blizzard graph — the node prompt is your charter: it defines your step's
scope, the assets it produces, and how you end. For the *method* inside that charter, strongly prefer the workspace's
installed winter workflows, keyed off the concepts your node itself names: reach into any extensions providing workflows
around those concepts — **planning**, **plan review**, **individual builds**, and **verification** are examples; there
may be more — and utilize those as appropriate. Each extension's `index.md` routes you to them; an extension may also
declare a map from node-steps to its processes — follow it when one exists.

Do **not** use an end-to-end workflow that resembles a full blizzard graph — any process that owns the whole plan →
build → review → deliver spine — unless your node prompt explicitly tells you to. The graph already *is* that process;
your node is one step of it. Where a node prompt's inline shorthand and the routed methodology differ in method, the
methodology is current — the charter still owns scope, assets, and endings.

The long commands this workspace runs are exactly what the preamble's background-work rule above is about — a repo's
task-runner gate, its test tiers, wheel builds, migrations. Run them in the foreground, or poll them to completion
before your turn ends.

## Then do the work

Keep your changes scoped to your assigned environment and to what the task asks. When the work is done — or if you hit
the stop condition above — end cleanly.
