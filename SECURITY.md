# Security policy

Blizzard runs coding agents without a person approving every tool call. That is necessary for an unattended fleet, but
it means an agent's judgment is not a security boundary. Operators need to know what a worker can reach when it makes a
bad choice, not just what the prompt asked it to do.

## What is true today

Workspace providers give chunks separate working environments; that separation is for organizing work, not an operating
system sandbox. A runner's workers can execute arbitrary tools with the access of the account running them. Claude
Code's scaffolded runner configuration uses `bypassPermissions`; unattended OpenCode runs use `--auto`, with explicit
tool denials in runner-owned configuration. These modes avoid waiting for an approval that no one is present to give.
Their deny rules can prevent particular tool calls, but they do not confine everything a shell command or its
descendants can do.

Do not treat an unattended runner on a shared host as confined merely because its agents received different worktrees.
Run it with an account, workspace, network access, and credentials appropriate for code executed by those agents.

## The boundary we are working toward

The useful trust unit is a **runner's workspace**. Multiple agents working there are collaborators: they may use its
repositories, build tools, services, and shared state. The aim is to contain the runner *and its worker process trees*
within the workspace's intended host resources, rather than promise that agents sharing that workspace cannot see one
another. A sandboxed runner would let its children inherit that boundary and keep ordinary process supervision intact.

The workspace provider supplies the environments; it does not prescribe how services are run. An agent should still be
able to read logs, call its local test APIs, fetch dependencies, use its model provider, and complete a Git workflow.
Winter's tmux sessions are one possible service implementation, not a capability every Blizzard deployment must expose.
The operator owns the local boundary; the hub can match reported capability, not configure host isolation.

This is a design direction, **not a sandbox Blizzard currently enforces**. Bubblewrap is one Linux candidate; macOS
needs an implementation with equivalent behavior. The runtime must be proven with real workspace preparation, worker
tools, networking, resume, interruption, and crash recovery before we claim the boundary exists.

## What the workspace boundary does not do

Putting the runner and workers in one sandbox does not separate them *from each other*. The runner needs a store, a hub
credential, and provider-specific authority to prepare workspaces; those resources cannot be assumed private from agents
inside the same boundary. An operator who needs to protect runner authority or one agent's environment from another
needs a narrower trust unit or another enforcement layer. Sandbox path rules alone do not restrict internet or local
service access either; any network limits must be enforced and tested separately.

Harness permissions are useful for refusing specific actions, but an unattended worker still needs a non-interactive way
to use its tools. Tightening those rules should follow the work an agent must actually perform, with explicit denies
where they help; it does not replace containment at the process boundary.

Finally, local isolation cannot protect remote Git history by itself. Workers must be able to commit, rebase, and push
their feature branches, including `--force-with-lease` after a rebase. The repository owner protects its default branch
at the forge and gives workers credentials without branch-protection bypass rights. Commit author identity, push
authentication, and commit signing are separate grants; none calls for the operator's personal keyring.
