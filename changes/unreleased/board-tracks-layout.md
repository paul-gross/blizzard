---
kind: added
surfaces: [http-api, wire, board]
---

The board gains a Tracks layout beside Board and List: a timeline of every runner host, workspace and environment, with
one block per node step a chunk spent there and what each environment holds now. Selecting a block opens the step: where
it ran, its spend, model, artifacts and escalations, its session's transcript, and the chunk's whole path across
environments. `GET /api/env-timeline` answers the steps over a window of up to fourteen days, or each environment's
newest with `latest`; `GET /api/env-timeline/step` answers one step by its session, its start, or the chunk's newest;
and `GET /api/env-timeline/step/transcript` answers the transcript of the session that ran a step.
