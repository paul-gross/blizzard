---
kind: added
surfaces: [http-api]
---

Every response that serves a graph view — `GET /api/graphs/{id}`, the mint, retire, enable, and patch responses, and
the deprecated follow-latest alias — now carries `definition_yaml`, the graph's source exactly as it was minted. Posted
back to `POST /api/graphs`, unchanged or edited, it mints a new version. A newly minted `default` graph stores its
prompts already inlined, so its body round-trips too.
