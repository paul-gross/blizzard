---
kind: removed
surfaces: [store]
retires: deprecate-forge-address-columns
breaking: true
breaks:
  - repositories.forge_api_url
  - work_sources.api_base
  - work_sources.web_base
  - repositories.forge_id
  - work_sources.forge_id
  - repositories
  - work_sources
upgrade-guide: forges-replace-the-address-fields
---

Removes the hub store's `repositories.forge_api_url`, `work_sources.api_base` and `work_sources.web_base` columns. Every
repository and work source must name a forge of its own tenant: `forge_id` is required and refers to `forges`, and a
repository's coordinate and a work source's locator are unique per forge.

## Upgrade

The migration refuses a store holding a repository or work source with no forge, a missing one, or another tenant's,
naming each record; point each at a forge with `--forge` before upgrading. To walk back, run the downgrade on this
release's image first: it fills the columns from each record's forge, and refuses a tenant holding one work-source
provider and locator on two forges, naming each pair, until one of each pair is changed.
