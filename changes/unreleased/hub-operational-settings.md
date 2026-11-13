---
kind: added
surfaces: [http-api, cli, config, store]
upgrade-guide: hub-settings-boundary
---

Adds hub-wide operational settings, with live edits, history, and TOML export and apply for hub administrators. Stored
live entries take precedence over the initial file seed; boot settings remain restart-based and tenant settings remain
separate.

## Upgrade

Run `blizzard hub migrate` before starting the upgraded hub. The first start seeds explicitly configured live file keys
that have no stored entry. Before rollback, export live values and copy the intended configuration into the file;
defaults and boot settings are not included in the export.
