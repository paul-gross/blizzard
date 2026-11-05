---
kind: removed
surfaces: [cli]
retires: deprecate-record-marker
breaking: true
breaks:
  - hub record-marker
upgrade-guide: runners-below-04-are-refused
---

Removes `blizzard hub record-marker`, which refused since 0.3: a hub node's action records its own markers.

## Upgrade

Drop any call to `blizzard hub record-marker` from your own scripts; it now fails as an unknown command.
