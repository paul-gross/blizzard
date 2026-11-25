---
kind: fixed
surfaces: [http-api, cli]
---

`blizzard hub graph sync` no longer re-enables a fully retired packaged graph whose stored definition the hub can no
longer read: it mints nothing, reports the graph `failed`, and exits non-zero until one of its mints is re-enabled.
