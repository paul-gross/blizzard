---
kind: deprecated
surfaces: [http-api, cli, config]
removes-in: "0.6"
---

A routine create that states a `name` without a `slug` — on `POST /api/projects/{project}/routines` or the deprecated
`POST /api/routines` — and a configuration document routine entry keyed by `name` alone are deprecated. Each takes the
name as the routine's slug today. State the `slug`, with a `name` when a different display name is wanted, on every
create and every entry. A `blizzard hub routine create` from a release before the routine slug sends only the name;
upgrade the CLI, whose `routine create` states the slug.
