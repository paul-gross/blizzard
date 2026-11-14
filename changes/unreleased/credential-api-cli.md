---
kind: added
surfaces: [http-api, cli]
---

A GitHub token credential can be managed over the API and the CLI: `/api/credentials` creates, lists, reads, edits,
retires and enables one, and `blizzard hub credential` does the same with `create`, `list`, `get`, `edit`, `retire` and
`enable`. A repository and a work source name the credential they act with by `credential` (`--credential` on `repo` and
`source` `create` and `edit`), and their reads carry `credential_id`, `credential_slug` and `credential_name`. A
credential edit that moves its secret moves every record naming it, and one a record still names cannot be retired.
