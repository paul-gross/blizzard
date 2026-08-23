# Remote runner

This document owns the runner-side pointing at a hub on another machine — the `hub_url`, the runner's identity, and what
network distance changes. It works against any reachable hub: the reference compose deployment
([`docs/install.md`](./install.md)), the colocated systemd install ([`docs/deployment.md`](./deployment.md)), or any hub
you can `curl`.

Nothing dials into the runner — it reaches the hub outbound-only and introduces itself, so a machine behind NAT or with
no inbound firewall rule is fine: no port to open, no address the hub needs to know.

## Install

On a runner-only machine, follow [`docs/deployment/install.md`](./deployment/install.md)'s "First install" for the
service account, the wheel venv, and the runtime-dir seeding — but skip `blizzard-hub init` (a runner machine hosts no
hub) and install only the `blizzard-runner.service` unit. Workspace and harness bindings are configured exactly as in
the colocated install; distance changes none of that.

## Point it at the hub

Only the config differs from the colocated case — in `blizzard-runner.toml`, two keys do the pointing:

- `hub_url = "https://hub.example.net"` — the hub's front door, not localhost. Give the runner the hub's TLS front door,
  not a bare container port: the enrolled bearer token rides every call and deserves transport encryption.
- `runner_id = "anna-laptop"` — unique per runner in the fleet.

Choose the final `runner_id` before the first start: it defaults to `runner-local`, every runner on a multi-runner hub
needs its own, and enrollment binds the bearer token to the exact id — under `runner_auth_mode = "enforce"` a token
presented for a different id is rejected.

The toml's `hub_url` is authoritative: `BZ_HUB_URL` seeds it only at `blizzard-runner init` time, and a running daemon
reads the toml alone — re-pointing a runner means editing the file and restarting, not exporting a variable. The same
`BZ_HUB_URL` variable does live-target the operator's `blizzard hub …` client CLI — two consumers, two behaviors; the
client obeying the variable does not mean the daemon does.

## Enroll

The enrollment sequence, the auth rollout modes, and where the token lives are owned by
[`docs/deployment/runner-auth.md`](./deployment/runner-auth.md); the remote case changes only how each step reaches the
hub. Enrollment presumes registration — the runner must have been started once so it registers at the hub, per that
owner, or the enroll call 404s.

Enroll from any operator machine:

```bash
blizzard hub runner enroll anna-laptop --hub-url https://hub.example.net
```

Operator verbs take the hub by URL — `--hub-url` on each call, or `BZ_HUB_URL` in the shell — and on a hub with
`auth.mode = "oauth"`, `blizzard hub login --hub-url …` comes first.

Install the token on the runner machine in the environment variable its `token_env` key names
([`docs/deployment/runner-auth.md`](./deployment/runner-auth.md)) — the unit's `EnvironmentFile` is the natural place —
then restart the runner.

A brand-new runner cannot join a hub already at `runner_auth_mode = "enforce"` unaided: registration itself is
authenticated under `enforce`, and enrollment requires a prior registration. The operator bridges an enforcing hub by
hand: set it to `runner_auth_mode = "warn"` and restart, let the new runner register, enroll it, install the token, then
re-enforce — keeping the window short, since it relaxes enforcement for the whole fleet, not just the newcomer.

## Verify

```bash
blizzard hub runner list --hub-url https://hub.example.net
```

The runner shows `online` (`--json` carries `last_seen_at`), and the board's fleet column agrees.

## What distance changes

An unreachable hub is routine, not an incident — the runner rides it out; what buffers and what keeps running is
[`docs/upgrade.md`](./upgrade.md)'s contract.

Two machines upgrade at two times, so version skew becomes possible; the supported window is
[`docs/versioning.md`](./versioning.md)'s.

A runner whose machine-local panel is opened from a browser on another machine needs the runner-side federation
configuration owned by [`docs/deployment/human-auth.md`](./deployment/human-auth.md)'s "Federating a runner's web
surface" — the origins `public_url` must declare, and the proxy settings an off-host origin needs. A runner driven
purely by the fleet, or whose panel is only opened on its own host, needs none of it.
