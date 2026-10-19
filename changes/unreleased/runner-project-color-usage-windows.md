---
kind: added
surfaces: [http-api, wire, store]
---

A runner now reads each served project's color from its hub and reports it in the dashboard's `served_projects`, so the
runner panel tints chunks by project as the board does. The host's subscriptions read carries each subscription's newest
usage windows and when they were sampled, from the runner's own store.
