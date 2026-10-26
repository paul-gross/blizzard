---
kind: deprecated
surfaces: [store]
removes-in: "0.4"
---

The hub store's `repositories.forge_api_url`, `work_sources.api_base` and `work_sources.web_base` columns are deprecated.
Each record names its forge by `forge_id`; the hub keeps the three columns equal to that forge's addresses, so a hub
walked back past the forge migration still reads them.
