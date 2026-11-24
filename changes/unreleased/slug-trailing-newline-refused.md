---
kind: fixed
surfaces: [http-api]
---

A scope or routine slug ending in a newline, such as `nightly\n`, is now refused like any other malformed slug. Before,
the hub accepted it and stored the newline as part of the slug.
