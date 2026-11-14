---
kind: added
surfaces: [http-api, cli, board]
---

A configuration document can declare credentials. Version 3 adds a `credentials` section, and each work source and
repository entry names the credential it acts with as `credential`, by id or slug, in place of `secret` and
`secret_name`. `POST /api/config/apply` and `blizzard hub config apply` take version 2 or 3, and
`GET /api/config/export` and `GET /api/config/schema/{kind}` take `version`, still 2 by default, as does
`blizzard hub config export --version 3`. A version-2 document keeps applying as before. `GET /api/credentials` now
counts the enabled records naming each credential as `referrer_count`, and lists every binding a credential may name
with its forge kind and settings as `bindings`. The board's Admin page gains a Credentials tab to create, edit, retire
and enable credentials, and its repository and work-source forms pick a credential of the record's forge in place of
typing a token secret.
