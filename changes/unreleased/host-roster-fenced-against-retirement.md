---
kind: fixed
surfaces: [http-api]
---

A host retire could lose a runner from under it, or a runner could be left live under a retired host, when another host
or runner verb landed while the retire was in flight. `POST /api/hosts/{host_id}/releases` now refuses `409` to release
a runner a retired host's retire has not retired yet, naming the host; re-run `POST /api/hosts/{host_id}/retire` to
finish it, then release. `POST /api/runners/{runner_id}/reinstate` refuses `409` while the runner stands under a retired
host. An adoption, a host add that adopts runners, a release, a reinstate, a host retire, a runner retire, or a host's
`POST /api/fleet/host/runners` that was decided before a competing retire or reinstate landed is refused rather than
written: `409` on the operator routes, and `403` on the host route when the host retired meanwhile. A release naming a
runner that moved to another host meanwhile is refused `404` and releases none of them. A host retire now retires the
runners standing under the host when its own retirement lands, so a runner moved to another host before then is left
alone. Run a refused verb again to decide it on the current state.
