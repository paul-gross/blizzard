---
kind: added
surfaces: [http-api, wire, store]
---

A runner now reads each served project's color from its hub and reports it in the dashboard's `served_projects`, so the
runner panel tints chunks by project as the board does. The host's subscriptions read carries each subscription's newest
usage windows and when they were sampled, from the runner's own store. Each lease in the runner's lease reads names its
chunk's project as `project_id`, closed leases included, so the panel attributes a chunk to its project without
guessing. Facts, asks and escalations name their chunk's project as `project_id` too.
