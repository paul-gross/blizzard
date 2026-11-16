# OpenCode 2 health: manual runner

This tool replaces only the `<env>/runner` service for the duration of a command. It copies the stopped runner's runtime
into lease scratch, disables Claude, and serves the candidate production runner at the normal <env> port with the fenced
engine-backed mock OpenCode 2. The default scenario uses `NEVER_REGISTERED`; set `OPENCODE2_HEALTH_SCENARIO=available`
to emit the mock without that lever. The normal service is restarted on completion, failure, SIGINT or SIGTERM. Do not
run concurrently with another user of <env>. SIGKILL or machine failure cannot be caught; in that case run
`winter service up <env>/runner --wait` manually.

The copy refuses symlinks and special files (other than skipped sockets). The command and candidate run in their own
process groups; cleanup sends TERM then KILL after two seconds. Do not run commands that detach into a new session:
their detached descendants are outside those groups and cannot be cleaned up by this tool.

Provision and start <env> first. From the workspace root, then from `<env>/blizzard`:

```sh
winter provision <env>
winter service up <env> --wait
source <(winter env <env> --resolve)
export BLIZZARD_TMPDIR=/absolute/path/to/lease/scratch
cd <env>/blizzard
node --experimental-strip-types tests/harness/manual/opencode2-health.ts sh -c 'curl -fs "http://127.0.0.1:$BZ_RUNNER_PORT/api/host/runners"'
```

Replace the command with the manual browser/HTTP exercise; it must remain running for the entire exercise. The tool
prints the private evidence directory and exports it to the command as `OPENCODE2_HEALTH_DIR`. It contains a copy of the
host runtime, including credentials: protect it and do not publish it. Logs, mock binary and isolated home are also
there. The copied store's scratch binding is redirected to a private empty root, so candidate reconciliation never
sweeps the original root; copied synthetic leases do not retain their original scratch contents. The original runtime is
not edited. In the default scenario the mock cannot register a service, so the OpenCode health result should report
unavailable; the available scenario should report available. Wait for the health probe to finish: `NEVER_REGISTERED`
waits for a 30-second registration timeout, and the initial dashboard response can precede the result. This setup alone
is not verification of the UI.
