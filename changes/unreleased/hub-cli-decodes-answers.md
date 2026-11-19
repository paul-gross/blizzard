---
kind: changed
surfaces: [cli]
---

A `blizzard hub` verb that cannot read the hub's answer now exits with one line naming the operation and the hub, such
as `GET /chunks/{id}: unreadable answer from the hub at http://127.0.0.1:8421 (1 validation error)`, instead of a Python
traceback. The line carries no value from the answer and no credentials from the hub URL. `--json` output is unchanged.
