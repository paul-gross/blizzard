---
kind: added
surfaces: [board, http-api]
---

The runner panel has a Settings tab showing the host's and each runner's settings with their revision in force, pending
revision and failed rebuild, plus the host's workspaces, subscriptions and settings history. The host's superuser edits
settings and creates, edits, retires and enables workspaces and subscriptions there, while other users only read. The
runner's `GET /api/auth/session` now reports `superuser`, whether the session holds the host's superuser authority.
