# Remote runner — a runner on a machine that is not the hub's

How to add a runner machine to a fleet whose hub it reaches over the network — the
[reference compose deployment](./install.md), the colocated systemd install in [`docs/deployment.md`](./deployment.md),
or any hub you can `curl`. This page owns the runner-side pointing: the `hub_url`, the runner's identity, and what the
distance changes. The enrollment mechanics and auth rollout modes it leans on are owned by
[`deployment/runner-auth.md`](./deployment/runner-auth.md)'s "Runner authentication"; the outage ride-out contract by
[`docs/upgrade.md`](./upgrade.md).

## What the distance changes — and what it doesn't

- **Nothing dials into the runner.** The runner reaches the hub outbound-only, so a laptop behind NAT, a home server
  behind a router, or a box with no inbound firewall rule at all are equally fine. There is no port to open and no
  address the hub needs to know — the runner introduces itself.
- **Reach the hub over HTTPS.** The reference deployments terminate TLS in front of the hub; give the runner that front
  door (`hub_url = "https://hub.example.net"`), not a bare container port. The runner's enrolled bearer token rides
  every call, and it deserves transport encryption.
- **Version skew becomes possible.** Two machines upgrade at two times; the supported window is
  [`docs/versioning.md`](./versioning.md)'s.
- **An unreachable hub is routine, not an incident.** The runner is built to ride out a hub that stops answering; what
  buffers and what keeps running is [`docs/upgrade.md`](./upgrade.md)'s contract.

## Point the runner at the hub

Install the wheel and seed the runtime directory as in [`deployment/install.md`](./deployment/install.md)'s "Install" —
steps 1–3 (venv, service account, `blizzard-runner init`; skip the `blizzard-hub init` line, a runner machine hosts no
hub) and step 5 for the `blizzard-runner.service` unit alone. Only the config differs from the colocated case. Two keys
in `blizzard-runner.toml` do the pointing:

```toml
# blizzard-runner.toml on the runner machine
hub_url   = "https://hub.example.net"   # the hub's front door, not localhost
runner_id = "anna-laptop"               # unique per runner in the fleet
```

- **`hub_url` lives in the toml, and the toml is authoritative.** `BZ_HUB_URL` seeds `hub_url` only at
  `blizzard-runner init` time — a convenience for scaffolding, not a live override. A running daemon reads the toml
  alone, so re-pointing a runner means editing the file and restarting, not exporting a variable. (The same `BZ_HUB_URL`
  name *does* live-target the operator's `blizzard hub …` client CLI — two consumers, two behaviors; don't let the
  client obeying the variable convince you the daemon does.)
- **Choose `runner_id` before the first start.** The id defaults to `runner-local`; on a multi-runner hub every runner
  needs its own. Enrollment binds the bearer token to this exact id — under `runner_auth_mode =
  "enforce"` a token
  presented for a different id is rejected — so pick the final name first and register under it.
- The runner's workspace and harness bindings are configured the same as ever
  ([`deployment/install.md`](./deployment/install.md), "Install" step 4); none of that changes with distance.

## Register, enroll, authenticate

The sequence, the auth modes, and where the token lives are [`deployment/runner-auth.md`](./deployment/runner-auth.md) —
read it beside this list. What follows is only what the distance adds to each step:

1. **Start the runner once so it registers** — same as colocated; the runner introduces itself on its own pull, just
   over the network now.
2. **Enroll it — against the remote hub.** From any operator machine:

   ```bash
   blizzard hub runner enroll anna-laptop --hub-url https://hub.example.net
   ```

   The operator verbs take the hub the same way the runner does — by URL: `--hub-url` on each call, or `BZ_HUB_URL` in
   the shell. On a hub with `auth.mode = "oauth"`, `blizzard hub login --hub-url …` first.
3. **Install the token on the runner machine**, in the variable `token_env` names (`deployment/runner-auth.md`, "The
   runner's outbound token") — the unit's `EnvironmentFile` is the natural place. Restart the runner.
4. **Verify**: `blizzard hub runner list --hub-url …` shows the runner `online` (`--json` carries `last_seen_at`), and
   the board's fleet column agrees.

**Joining a hub that already enforces runner auth.** Registration itself is authenticated once
`runner_auth_mode = "enforce"`, and enrollment requires a prior registration — so a brand-new runner cannot join an
enforcing hub unaided. Today the operator bridges it by hand: set the hub back to `runner_auth_mode = "warn"` and
restart it, let the new runner register, enroll it, install the token, then re-enforce. Mind that the window relaxes
enforcement for the whole fleet, not just the newcomer — keep it as short as those steps.

## If humans will open this runner's panel

The runner's own machine panel is a separate, runner-local surface. Reaching it from a browser on another machine — and
signing in through the hub's SSO — is [`docs/deployment/human-auth.md`](./deployment/human-auth.md) §Runner-side
federation, which owns the whole procedure: which origins `public_url` must declare, why the browser rather than the hub
is what follows them, and the two proxy settings an off-host origin needs. The distance-specific consequence this page
owns is only that a runner reached from elsewhere needs that configuration at all — a runner driven purely by the fleet
needs none of it, and neither does one whose panel is only ever opened on its own host.

## Next

- **Enrollment modes, token rotation, rollout order**: [`docs/deployment/runner-auth.md`](./deployment/runner-auth.md).
- **What survives an outage or an upgrade**: [`docs/upgrade.md`](./upgrade.md).
- **The hub the runner is pointing at**: [`docs/install.md`](./install.md) for the compose shape,
  [`docs/deployment.md`](./deployment.md) for colocated.
