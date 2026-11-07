---
kind: changed
surfaces: [http-api, wire, cli]
---

The hub and runner command-line verbs now read a hub refusal through one problem-details reader. They dispatch on the
problem `type`, and show the refusal's `detail`, or its `title` when it has none, instead of per-verb text keyed on the
HTTP status. A refusal from a hub before problem details, which has no `type`, is still read from its released `detail`
and `detail.code` fields.

The hub's `404` for a route held behind a flag that is off is now an `application/problem+json` body of the new type
`urn:blizzard:problem:flag-off`; its `detail` is the same `{code, flag, message}` object as before. The hub
administrator invariant check's `409` is the new type `urn:blizzard:problem:invariants-in-progress`; it adds a `detail`
sentence beside the released `code` member.
