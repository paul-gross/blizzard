---
kind: added
surfaces: [http-api, cli, config, board]
---

A project's `color` accepts a custom `#rrggbb` hex beside the twelve palette names, through the projects API,
`blizzard hub project create|edit --color` and a config document. The hub stores it lowercase, and the board draws it
adjusted for legibility on its dark surfaces.
