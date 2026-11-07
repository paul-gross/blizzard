---
kind: deprecated
surfaces: [http-api, wire]
removes-in: "0.6"
---

Legacy refusal discrimination and the runner's fallback for old hubs that omit `type` are deprecated. Hub refusals now
identify their semantics with registered `urn:blizzard:problem:<refusal-name>` types. The legacy fields below remain
present with their existing types; current problem detail and needed diagnostic data are not scheduled for deletion.

## Upgrade

Dispatch on the problem `type` rather than inferring the refusal from legacy fields or detail text. An unknown present
type is unexpected; use the old-hub fallback only when `type` is absent. The retained deprecated extensions are:

- Claim conflict: `chunk_id` and `held_by_runner_id` are strings; use `claim-conflict`.
- Terminal and not-ready claims: `chunk_id` and `status` are strings; use `claim-terminal` or `claim-not-ready`.
- Dependency claims: `chunk_id` and `prerequisite_chunk_id` are strings; use `claim-dependency`.
- Incompatible claims: `chunk_id` and `incompatible_runner_id` are strings, and `reason` is an open string vocabulary;
  use `claim-incompatible` and retain `reason` where its diagnostic distinction is needed.
- Paused, retired, unregistered and version-refused claimants: `chunk_id` and `runner_id` are strings; use
  `claim-paused`, `claim-runner-retired`, `claim-runner-unregistered` or `claim-runner-version-refused` respectively.
- Runner identity refusal: `reason` is an open string vocabulary and `runner_id` is a string or null; use
  `runner-identity` and retain `reason` where its diagnostic distinction is needed.
- Release refusal: `chunk_id` is a string and `reason` is an open string vocabulary; use `release-refusal` and retain
  `reason` where its diagnostic distinction is needed.
- Login and SSO refusal: `error` is the string literal `login_failed` or `sso_refused`; use `login-failed` or
  `sso-refused` respectively.

The names above are suffixes of `urn:blizzard:problem:`. HTTP status remains authoritative. A future removal is a
separate compatibility-judged change; this deprecation does not raise the runner compatibility floor.
