---
kind: deprecated
surfaces: [http-api, cli]
removes-in: "0.6"
---

`secret_name` on a repository write, `secret` on a work-source write, and `--secret` on `blizzard hub repo` and
`blizzard hub source` `create` and `edit` are deprecated. Each still works: it resolves to the credential that reads the
secret, creating one when none does, and the write answers a `Deprecation` header, one `warnings` entry per old field,
and a `config-old-field-used` event. Name the credential with `credential` or `--credential` instead.
