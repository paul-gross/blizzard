---
kind: changed
surfaces: [http-api, wire, cli]
---

The hub and runner command-line verbs now read a hub refusal through one problem-details reader, dispatching on the
problem `type` and showing the refusal's `detail`, or its `title` when it has none. Every deliberate domain refusal on
the routes the CLI and the board call is now a registered `urn:blizzard:problem:<concept>-<condition>` type, declared in
the OpenAPI document with its status and `detail` unchanged. The runner relays a hub refusal on its proxied routes as
the hub sent it. A hub from before problem details is still read from its released `detail` fields.
