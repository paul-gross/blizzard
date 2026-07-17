# `fleet`

The shared fleet layer both blizzard web apps compose: the design layer (tokens,
scrollbars), the mission-control board views, the SSE transport and live-update
spine, the reads and mutations, and the generated API clients.

It is **not published** — it is built into the `hub` and `runner` apps, whose output
is embedded in the one blizzard wheel.

See [`../../README.md`](../../README.md) for the workspace: the project layout, the
scripts (lint, the vitest tier, build, client codegen), the dev server, the generated
API client, and the design layer.
