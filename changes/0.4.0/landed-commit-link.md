---
kind: added
surfaces: [board, http-api, wire]
---

A repository landed without a pull request now links its landed commit on the board and in the comment left on the
closed issue. The link is the commit's address on the forge of the repository record it resolves to, so
`landed_repos[].url` on `GET /api/chunks` and `GET /api/chunks/{chunk_id}` is no longer null for such a landing. A pull
request's own address still wins when there is one.
