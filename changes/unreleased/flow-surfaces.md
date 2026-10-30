---
kind: added
surfaces: [http-api, wire, cli, board]
---

Each branch's delivery line can now be read. `GET /api/flows`, `GET /api/flows/{flow_id}` and
`GET /api/flows/{flow_id}/record` answer each flow's open turn, the chunks in line with their state and position, its
last verdict and landing, and its record newest first; each chunk carries its `flow_places`, and the named chunk its
`flow_record`. A `flow-changed` frame announces each entry written to a record. `blizzard hub flow list` and
`blizzard hub flow show` print the same, and `blizzard hub chunk show` prints a chunk's places. The board gains a
Delivery band with one lane per active flow, opening a flow's record in the dock, and each delivering card and list row
says its place.
