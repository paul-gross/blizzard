---
kind: added
surfaces: [http-api, wire, cli, store, board]
---

A credential can act as a GitHub App installation: the `github.app-installation` binding takes the App's private key
from its secret, an `app_id` and an `installation_id`, and the hub mints and renews installation tokens itself. A
repository and a GitHub work source may name one, `blizzard hub credential create` and `edit` take `--app-id` and
`--installation-id`, and each setting `GET /api/credentials` lists for a binding now carries its `kind`.

## Upgrade

Rolling the store back below this release is refused while any App credential exists, naming each one. No hub command
deletes a credential, so before rolling back, stop the hub and remove each named credential by a direct store edit.
