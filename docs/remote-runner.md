# Remote runner

This document owns the runner-side pointing at a hub on another machine — the `hub_url`, the runner's identity, and what
network distance changes. It works against any reachable hub: the reference compose deployment
([`docs/install.md`](./install.md)), the colocated systemd install ([`docs/deployment.md`](./deployment.md)), or any hub
you can `curl`.

Nothing dials into the runner — it reaches the hub outbound-only and introduces itself, so a machine behind NAT or with
no inbound firewall rule is fine: no port to open, no address the hub needs to know.

## Install

On a runner-only machine, follow [`docs/deployment/install.md`](./deployment/install.md)'s "First install" for the
service account, the wheel venv, and the unit — but skip `blizzard-hub init` (a runner machine hosts no hub), install
only the `blizzard-runner.service` unit, and don't enable it yet. Stop before step 5: its `blizzard-runner init` names
no hub, so it would try to add the runner at the default `http://127.0.0.1:8421`. [Add](#add) runs init against the real
hub and starts the unit. Workspace and harness bindings are configured exactly as in the colocated install; distance
changes none of that.

## Point it at the hub

Only the config differs from the colocated case — in `blizzard-runner.toml`, two keys do the pointing:

- `hub_url = "https://hub.example.net"` — the hub's front door, not localhost. Give the runner the hub's TLS front door,
  not a bare container port: the runner's bearer token rides every call and deserves transport encryption.
- `name = "anna-laptop"` — the label `hub runner list` and the board show beside the runner's id. It identifies nothing:
  two runners may share a name, and every operator verb takes the id. It defaults to `runner-local`.

The toml's `hub_url` is authoritative: `BZ_HUB_URL` seeds it only at `blizzard-runner init` time, as `--hub <url>` does
over it, and a running daemon reads the toml alone — re-pointing a runner means editing the file and restarting, not
exporting a variable. The same `BZ_HUB_URL` variable does live-target the operator's `blizzard hub …` client CLI — two
consumers, two behaviors; the client obeying the variable does not mean the daemon does. Init refuses a `--hub` that
differs from the hub an existing config names.

The runner renames itself: change `name` and restart it, and its next registration shows the new name everywhere from
then on. Its id, token, pause state, and history stay as they were. A toml that declares a `runner_id` and no `name`
reads that value as the name.

## Add

A runner has no identity until the hub adds it and issues the token it proves that identity with;
[`docs/deployment/runner-auth.md`](./deployment/runner-auth.md) owns adding, where the token lives, and rotation. The
remote case changes only how each step reaches the hub.

On the runner machine, `init` adds the runner and installs its token in one step:

```bash
blizzard-runner init /var/lib/blizzard/runner --hub https://hub.example.net
```

On a hub with `auth.mode = "oauth"`, that add runs under your `blizzard hub login --hub-url …` session on the runner
machine, so it takes an operator holding `runner:add`. Where you would rather not sign in there, add the runner from any
operator machine instead:

```bash
blizzard hub runner add anna-laptop --hub-url https://hub.example.net
```

Operator verbs take the hub by URL — `--hub-url` on each call, or `BZ_HUB_URL` in the shell — and on a hub with
`auth.mode = "oauth"`, `blizzard hub login --hub-url …` comes first.

`add` prints the runner's id and, once, its token as a `BZ_HUB_TOKEN=…` line. Put that line in the runtime dir's `.env`
on the runner machine as the service account, under a umask that keeps the file owner-only (`0600`) — the token is the
runner's whole credential, so no other account may read it:

```bash
sudo -u blizzard mkdir -p /var/lib/blizzard/runner
sudo -u blizzard sh -c 'umask 077; echo "BZ_HUB_TOKEN=…" >> /var/lib/blizzard/runner/.env'
```

Then run the same `blizzard-runner init … --hub https://hub.example.net` there: it finds the token, confirms the hub
knows it, and adds nothing. It also makes a `.env` that other accounts can read owner-only, or warns when it cannot.

Give the runner its own session-signing secret too, so board sessions survive a restart: generate one with
`openssl rand -base64 48` and put it in the environment variable the `session_secret_env` key names (default
`BZ_RUNNER_SESSION_SECRET`) — the unit's `EnvironmentFile` is the natural place, since the daemon reads only the hub
token from the runtime dir's `.env`. It must be base64 decoding to at least 32 bytes — a shorter one fails config load
with an error naming the variable. Unset, the runner signs with a fresh random secret each start and logs that sessions
will not survive a restart. Never share one secret between runners: a cookie minted by one would verify on the other.

Then start the runner with `systemctl enable --now blizzard-runner`, or `systemctl restart blizzard-runner` if the unit
already runs: the daemon reads its token and session secret only at start.

## Verify

```bash
blizzard hub runner list --hub-url https://hub.example.net
```

The runner's row shows its id and name. It reads `never-connected` until the runner's first registration and `online`
after it (`--json` carries `last_seen_at`), and the board's fleet column agrees.

## What distance changes

An unreachable hub is routine, not an incident — the runner rides it out; what buffers and what keeps running is
[`docs/upgrade.md`](./upgrade.md)'s contract.

Two machines upgrade at two times, so version skew becomes possible; the supported window is
[`docs/versioning.md`](./versioning.md)'s.

A runner whose machine-local panel is opened from a browser on another machine needs the runner-side federation
configuration owned by [`docs/deployment/human-auth.md`](./deployment/human-auth.md)'s "Federating a runner's web
surface" — the origins `public_url` must declare, and the proxy settings an off-host origin needs. A runner driven
purely by the fleet, or whose panel is only opened on its own host, needs none of it.
