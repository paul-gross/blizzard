---
kind: fixed
surfaces: [http-api, board]
---

The runner panel no longer shows a chunk's older, exited attempt as its current one when a newer attempt has closed, and
no longer shows a running heartbeat on a worker that has exited. The runner's lease and subscription reads now say
whether a lease has ended and whether a subscription's credential has lapsed, and the panel follows them.
