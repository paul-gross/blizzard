---
kind: added
surfaces: [config]
---

`[runner.worker]` `shell` names the absolute path of the shell a worker's Bash tool runs under, defaulting to
`/bin/bash`. Claude Code workers receive it as `CLAUDE_CODE_SHELL` and OpenCode workers as the `shell` key of the
runner-owned worker config, so a host with zsh installed no longer hands workers zsh. `runner host` refuses to start
when the configured shell is missing or not executable.

## Upgrade

Workers now run under bash unless `shell` says otherwise. Run `runner init` to refresh the OpenCode worker config.
