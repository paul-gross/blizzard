---
kind: added
surfaces: [http-api, config, cli, store]
---

Adds urgencies: each tenant's ordered set, most urgent first, with one live urgency the tenant's settings name as the
default every new chunk takes when nothing names another. Every tenant starts with `standard` as its only urgency and
its default, and every chunk carries an urgency. An urgency has a fixed slug, a name, a color, and an optional emoji or
icon mark. Urgencies are created, listed, edited, reordered, retired with their open chunks moved to another, and
enabled over `/api/urgencies`; the default is shown and changed over `/api/settings`. The same verbs are
`blizzard hub urgency` and `blizzard hub settings`, and a configuration document's `urgencies`, `urgency_order`, and
`settings` sections declare them. The change log records the `urgency`, `urgency_order`, and `tenant_settings` kinds,
which `blizzard hub config changes --kind` now accepts. A retire's `urgency-is-default` and `urgency-in-use` refusals
carry a string `detail`, with `slug` as a member on both and `open_chunks` on `urgency-in-use`.

## Upgrade

Run `blizzard hub migrate` before starting the upgraded hub. It gives every tenant the urgency `standard` and settings
naming it as the default, and gives every chunk, finished or open, that urgency.
