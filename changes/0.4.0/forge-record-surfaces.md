---
kind: added
surfaces: [http-api, cli]
---

Adds `/api/forges` and `blizzard hub forge` for listing, creating, inspecting, editing, retiring and re-enabling forge
records. Forge details include linked repositories and work sources; configuration change diffs show current forge
labels beside forge ids.

Configuration documents now accept version 2 with explicit forge entries and forge references. The API's export and
schema routes still default to version 1; `blizzard hub config export` defaults to version 2, with `--version 1` for the
previous shape.
