---
kind: changed
surfaces: [http-api, wire, config, cli]
---

A chunk whose work reaches several repositories now lands them in its project's landing order: repositories a project
ranks land first, lowest position first, and every unranked repository lands after them in slug order. A project that
ranks nothing therefore lands its repositories by slug, where they used to land in the order the chunk's work first
named them.

`blizzard hub project order <project> <repository>…` sets a project's whole landing order, and
`blizzard hub project position <project> <repository> (--after <repository> | --first | --unranked)` moves one
repository. `blizzard hub project show` lists the repositories in that order, each with its position or "unranked". The
API reads the order on `GET /api/projects/{project}/repository-links`, now carrying each link's `position`, and writes
it with `PUT /api/projects/{project}/landing-order` and `POST /api/projects/{project}/landing-order/position`. Both
writes need the config-edit permission and refuse a repository the project does not link with
`422 repository_not_linked`.

A repository carries a `landing_policy`, `merge-on-checks` by default, and an optional `park_timeout_hours`.
`blizzard hub repo create` and `edit` take `--policy` and `--park-timeout`, `edit` takes `--clear park-timeout`, and
`repo show` prints the policy. A `PATCH` may send `null` for `park_timeout_hours` to clear it; `null` on any other field
is still refused. `GET /api/config/schema/repositories` lists each policy with the forge capabilities it requires, and
`blizzard hub forge show` and a forge's detail list the capabilities the forge offers. The configuration document
carries `landing_policy` and `park_timeout_hours` on a repository entry and `landing_order` on a project entry; export
writes `landing_order` only for a project that ranks a repository.
