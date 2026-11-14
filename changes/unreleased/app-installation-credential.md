---
kind: added
surfaces: [http-api, wire, cli, store, board]
---

A credential can act as a GitHub App installation. The `github.app-installation` binding reads the App's private key
from its secret and takes `app_id` and `installation_id`, each a positive integer; the hub mints, renews and narrows
installation tokens itself, and every credential naming one installation shares one budget and one write lane, under the
authority `app/<app_id>/installation/<installation_id>`. `blizzard hub credential create` and `edit` take `--app-id` and
`--installation-id`, and a version-3 configuration document declares one as any other credential. Each setting
`GET /api/credentials` lists for a binding now carries its `kind`, `choice` or `positive-integer`. The store revision
this adds changes no table, and its downgrade is refused while an App credential exists. A repository and a GitHub work
source may each name an App credential. Reading checks needs the installation to grant both Checks read and Commit
statuses read.
