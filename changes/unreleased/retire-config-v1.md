---
kind: removed
surfaces: [http-api]
retires: config-v1-deprecation
breaking: true
breaks:
  - ConfigDocument
  - RepositoryDocument
  - WorkSourceDocument
upgrade-guide: forges-replace-the-address-fields
---

Retires version 1 of the configuration document. `POST /api/config/apply` refuses any `version` but 2, a missing one
included, with `422 config_version_retired` naming version 2. `GET /api/config/export` and
`GET /api/config/schema/{kind}` answer version 2 by default and refuse `version=1` the same way, so
`blizzard hub config export --version 1` prints that refusal.

## Upgrade

Carry every kept configuration document onto version 2: declare its forges under `forges` and name each from its
repository and work-source entries with `forge`. `blizzard hub config export` writes one from the running hub.
