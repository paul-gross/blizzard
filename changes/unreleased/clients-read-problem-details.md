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

The runner now relays a hub refusal that carries a JSON object body to its caller as the hub sent it: the body, the
status, the `Content-Type` and the `WWW-Authenticate` and `Retry-After` headers. Both OpenAPI documents publish a base
`Problem` component, which the board reads for a refusal's `detail` and `title`.

Six more refusals the operator CLI reads are registered problem types with their status and `detail` unchanged:
`secret-already-exists`, `secret-not-found`, `decision-already-resolved`, `question-already-answered`,
`egress-backfill-failed` and `trace-replay-failed`. The question-already-answered `409` gains a `detail` sentence.

Ten more are registered with their status and `detail` unchanged: a cadence write's stale `If-Match` `409` is
`revision-conflict`; the ingest `409` of `POST /api/chunks`, `POST /api/work-sources/{source}/items`,
`POST /api/routines/{routine_id}/run` and `POST /api/garden-proposals/{proposal_id}/accept` is `pointer-already-held`;
the dependency `409`s are `dependent-not-editable` (whose `status` member stays the chunk's status),
`dependency-would-close-cycle`, `prerequisite-ephemeral` and `no-standing-dependency`; `POST /api/config/apply` answers
`config-apply-conflict` (`409`), `config-media-type-unsupported` (`415`) and `config-document-invalid` (`422`, its
located `detail` list unchanged), which `GET /api/config/export` and `GET /api/config/schema/{kind}` also answer for a
retired version; and `POST /api/graphs`'s `422` is `graph-definition-invalid`, which gains a `detail` sentence beside
its released `ok`, `errors` and `warnings`.
