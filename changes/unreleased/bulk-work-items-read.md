---
kind: added
surfaces: [http-api, wire]
---

The hub serves `GET /api/work-items`: the pass-through work items of up to 100 chunks in one read, named by a repeatable
`chunk_id` and answered as each chunk's entries, exactly what `GET /api/chunks/{chunk_id}/work-items` gives it. Comments
are empty unless `include_comments=true`; an unknown chunk id is omitted, and more than 100 ids answer 422. A GitHub
work source is asked once per read through one GraphQL request, however many of its items the chunks hold, and a source
it cannot read errors only its own entries.
