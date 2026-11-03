---
kind: fixed
surfaces: [http-api]
---

A runner retire could undo the rescue of a runner reinstated while it ran: a retire re-run, or a host retire reaching a
runner released and reinstated meanwhile, revoked the token the runner was enrolled with since and released the chunks
it claimed since, and still answered `200`. `POST /api/runners/{runner_id}/retire`, `POST /api/hosts/{host_id}/retire`
and a host's `POST /api/fleet/host/runners/{runner_id}/retire` now answer `409` instead, writing nothing more; what the
retire committed first is still announced. `POST /api/runners/{runner_id}/reinstate` now releases any chunk a retire
stopped before its releases left held, publishing each `detached` frame, before it reverses the retirement.
`POST /api/runners/{runner_id}/token-revocations` answers `409` when the runner was retired or reinstated while the
revocation was decided. Run a refused verb again to decide it on the current state; a refusal keeps whatever the verb
wrote before it. `blizzard runner retire` prints the hub's refusal as the hub gave it, rather than reading every refusal
as a runner still holding chunks.
