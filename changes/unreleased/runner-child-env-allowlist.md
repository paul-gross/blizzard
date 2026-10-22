---
kind: security
surfaces: [config]
---

Every child the runner launches now gets its environment from the worker allowlist, not only worker, judge and resume
sessions. That includes the runner's own `git` and `winter` calls, harness version probes and node `checks:` commands.
These children get the base allowlist, `[runner.worker]` `env_passthrough` and `path_prepend`, without a lease's
identity, token or tracing variables, so a daemon credential such as `BZ_HUB_TOKEN_<NAME>` no longer reaches them.
A timed-out check now kills every process it started, not just its shell.

## Upgrade

If your `git`, `winter` or `checks:` commands relied on a variable inherited from the runner's environment, such as
`SSH_AUTH_SOCK` for SSH remotes, name it in `[runner.worker]` `env_passthrough` in `blizzard-host.toml` before
upgrading the runner.
