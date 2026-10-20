---
kind: changed
surfaces: [http-api]
breaking: true
breaks:
  - HostRegistrationState
  - IdentityState
---

The runner API's `HostRegistrationState` and `IdentityState` responses gain the enum value `version_refused`, which a
runner reports while the hub refuses its version.

## Upgrade

A client of the runner API must expect `version_refused` in `HostRegistrationState` and `IdentityState`, and treat it
as a runner that is registering again, not as a failure.
