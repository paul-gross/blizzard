---
kind: fixed
surfaces: [http-api, cli]
---

Two takeovers entered on the same chunk at the same moment no longer both open. The second is refused with `409` because
the chunk already has an open takeover, and it leaves no takeover behind.
