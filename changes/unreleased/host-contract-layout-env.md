---
kind: added
surfaces: [config]
---

`contracts/config/host.json` now names every file under the runner host directory — `.env`, `runners/<name>`,
`worker-settings.json`, `plugins/blizzard-heartbeat.ts`, `opencode-worker-config.json` — with the `blizzard-runner`
verbs that write each and the mode the host enforces, and a top-level `env` section for `BZ_HOST_TOKEN` and
`BZ_HUB_TOKEN_<NAME>` with the rule that derives the latter. Every path row and both documents gain `written_by`, `mode`,
and `env`; existing fields are unchanged.
