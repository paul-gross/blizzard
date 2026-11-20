---
kind: changed
surfaces: [config, cli]
breaking: true
upgrade-guide: workspaces-and-subscriptions-become-records-in-the-host-store
---

A host no longer serves a default `workspace-local` when it declares no workspace: it serves only the workspaces its
store holds. `blizzard runner init` still binds its runner's projects to `workspace-local` but writes no
`[[workspace]]`, and no longer reads `BZ_WORKSPACE_ROOT`, `BZ_WORKSPACE_ENVS`, `BZ_WORKSPACE_PROMPT`,
`BZ_WORKSPACE_PROMPT_PACKAGE` or `BZ_BASE_BRANCH`. A runner bound to a workspace the host does not serve, or naming a
subscription it does not hold, keeps running and claims nothing through that binding; a runner settings write naming
either is refused. Once a host has seeded a `[[workspace]]` or `[[subscription]]` from its file, editing or removing it
in the file no longer changes the host.

## Upgrade

Before upgrading, declare a `[[workspace]]` in the host file for every workspace a `[[runner]]` binds —
`id = "workspace-local"` on a host that declares none — or create it after the upgrade with
`blizzard runner workspace create <id> KEY=VALUE…`. Script any `init` that relied on `BZ_WORKSPACE_*` to write that
`[[workspace]]` itself. After the first start, change a workspace or subscription with `blizzard runner workspace` or
`blizzard runner subscription`, not the host file.
