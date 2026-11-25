---
kind: added
surfaces: [board, http-api]
---

The runner panel has a Settings tab. It shows the host's and each runner's settings with the revision in force, any
pending revision and any failed rebuild, the host's workspaces and subscriptions with their history, and the settings
history. The host's superuser edits settings and creates, edits, retires and enables workspaces and subscriptions from
it; other users only read. The runner's `GET /api/auth/session` now reports `superuser`, whether the session holds the
host's superuser authority.
