---
kind: added
surfaces: [cli, http-api]
---

Adds `blizzard runner token set [--runner NAME|ID]`, which reads a runner's hub token on stdin and seals it in the host
store, and the `PUT /api/host/runners/{runner_id}/token` route a running host serves it through. On a running host the
runner takes the token up at its next identity check, and a runner whose hub refused its token as retired is refused; on
a stopped host the token takes effect at the next start. `blizzard hub runner add` and `enroll` now print the token on a
line of its own, naming this verb.
