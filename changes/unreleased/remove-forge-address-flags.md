---
kind: removed
surfaces: [cli]
retires: deprecate-forge-address-flags
breaking: true
breaks:
  - hub repo create --forge-api-url
  - hub repo edit --forge-api-url
  - hub source create --api-base
  - hub source create --web-base
  - hub source edit --api-base
  - hub source edit --web-base
upgrade-guide: forges-replace-the-address-fields
---

Removes `blizzard hub repo`'s `--forge-api-url`, and `blizzard hub source`'s `--api-base`, `--web-base`,
`--clear api-base` and `--clear web-base`. Each is now refused as an unknown option or choice.

## Upgrade

Pass `--forge`, a forge's id or slug, in every script or runbook that passed one of these flags.
