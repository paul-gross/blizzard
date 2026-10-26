---
kind: added
surfaces: [wire, http-api, store, board]
---

A runner registration now reports the blizzard version it runs, and the hub judges it against its compatibility window.
A runner below the window's floor, or newer than the hub, is refused `409` with a `RunnerVersionRefusal` naming both
versions, the floor, and what to upgrade; it claims nothing until a later registration is admitted. An admitted
registration's answer carries the hub's version and window. `GET /api/runners` reports each runner's version and its
standing against the window. A hub whose own version is below its window's floor refuses to start. A refused runner
halts alone, leaving the host's other runners working, and registers again each tick until it is admitted; its status
reports `version_refused` with the hub's sentence, and the hub's version and window once admitted.

## Upgrade

Move the hub first. A runner whose version sits outside the hub's window is refused `409` and claims nothing until it is
upgraded or downgraded into the window, so the runner host follows the hub in the same pass. To go back, reinstall the
previous wheel and run `blizzard hub migrate --down` to the previous revision.
