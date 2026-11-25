---
kind: added
surfaces: [http-api, cli]
---

Tenant configuration, hub settings apply, and graph mint accept TOML documents alongside YAML and JSON. CLI file formats
are selected by extension; HTTP document formats are selected by Content-Type. Hub settings export defaults to TOML,
while tenant configuration export remains YAML or JSON.
