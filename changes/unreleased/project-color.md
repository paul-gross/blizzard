---
kind: added
surfaces: [http-api, cli, config, store, board]
---

Every project now has a color, one of twelve palette names. A project created without one takes the tenant's least-used
color, and `blizzard hub project create` and `edit` take `--color`. The board draws it on each chunk card, each project
lens chip, the Admin → Projects list and the project's page, where it can be changed. Projects that existed before are
given distinct colors when the hub upgrades.
