---
kind: changed
surfaces: [config, cli]
breaking: true
breaks:
  - The default `workspace-local` a host serves when its file declares no `[[workspace]]`
  - "`blizzard runner init` reading `BZ_WORKSPACE_ROOT`, `BZ_WORKSPACE_ENVS`, `BZ_WORKSPACE_PROMPT`, `BZ_WORKSPACE_PROMPT_PACKAGE` and `BZ_BASE_BRANCH`"
  - Host-file edits to a seeded `[[workspace]]` or `[[subscription]]`
upgrade-guide: workspaces-and-subscriptions-become-records-in-the-host-store
---

A host serves only the workspaces its store holds, with no default `workspace-local`, and `blizzard runner init` writes
no `[[workspace]]` and reads no `BZ_WORKSPACE_*` or `BZ_BASE_BRANCH`. A runner bound to a workspace or subscription the
host does not hold claims nothing through that binding, and a runner settings write naming one is refused. Once seeded,
a `[[workspace]]` or `[[subscription]]` edited or removed in the host file no longer changes the host.

## Upgrade

Before upgrading, declare a `[[workspace]]` in the host file for every workspace a `[[runner]]` binds —
`id = "workspace-local"` on a host that declares none — or create it after the upgrade with
`blizzard runner workspace create <id> KEY=VALUE…`. Script any `init` that relied on `BZ_WORKSPACE_*` to write that
`[[workspace]]` itself. After the first start, change a workspace or subscription with `blizzard runner workspace` or
`blizzard runner subscription`, not the host file.
