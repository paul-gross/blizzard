---
kind: changed
surfaces: [http-api]
breaking: true
breaks:
  - ChoiceTargetKind
  - HistoryRowView.kind
  - HostRegistrationState
  - IdentityState
  - ProjectDocument.landing_order
  - RepositoryDocument.landing_policy
  - RepositoryDocument.park_timeout_hours
---

Responses a supported release reads closed gained values and fields its clients survive: `ChoiceTargetKind` gains
`escalate`, `HistoryRowView.kind` gains `requeue`, `resolution` and `restart`, `HostRegistrationState` and
`IdentityState` gain `version_refused`, and the configuration document's project and repository entries gain
`landing_order`, `landing_policy` and `park_timeout_hours`. Each is listed as breaking because a `0.2` or `0.3` schema
forbids it; `contracts/compat/allowances.toml` records why each line's clients carry on.

## Upgrade

Nothing to do. A `0.2` board tab left open across the hub's upgrade may fail to draw a graph that escalates until it
reloads; a `0.2` runner panel shows the same reload behavior the 0.3.0 upgrade notes describe.
