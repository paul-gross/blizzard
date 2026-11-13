---
kind: changed
surfaces: [event-stream]
---

A repository whose token is fine-grained (`github_pat_…`) now escalates `forge-capability-missing` before a delivery
pass that needs to read checks, such as one under `merge-on-checks` or `fast-forward`, naming the token and sending
nothing to the forge. Hub start logs a warning for each such token, and a check read GitHub refuses for one is reported
as `token-unsupported`, with the capability it needs. The `forge-capability-missing` event gains `credential`, and the
budget notices gain `authority`.

## Upgrade

Before upgrading, replace any fine-grained token behind `merge-on-checks` or `fast-forward` with a classic token, or
move its repository to a landing policy that does not read checks. Otherwise its chunks escalate at their next delivery
pass; the hub's startup log names each token affected.
