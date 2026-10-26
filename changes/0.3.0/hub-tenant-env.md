---
kind: added
surfaces: [cli]
---

`blizzard hub` verbs that act in a tenant read it from `BZ_HUB_TENANT` when `--tenant` is not given, as `BZ_HUB_URL`
stands in for `--hub-url`. `--tenant` overrides the variable, and an empty value counts as unset.
