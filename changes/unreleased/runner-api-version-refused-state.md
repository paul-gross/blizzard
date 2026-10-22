---
kind: changed
surfaces: [http-api]
breaking: true
breaks:
  - HostRegistrationState
---

The runner API's `HostRegistrationState` and `IdentityState` responses gain the enum value `version_refused`, which a
runner reports while the hub refuses its version. Only `HostRegistrationState` is listed as breaking: `0.2` clients of
`IdentityState` handle the value without failing, though a `0.2` panel shows the runner's identity as "unchecked" and a
`0.2` `runner status` prints no identity line.

## Upgrade

After upgrading a runner host, reload any runner panel left open from before the upgrade: a `0.2` panel fails on
`version_refused` in `HostRegistrationState` until it reloads. A `0.2` panel still open shows a version-refused runner's
identity as "unchecked" until it reloads, and a `0.2` `runner status` prints no identity line for it.
