---
kind: deprecated
surfaces: [http-api, cli]
removes-in: "0.4"
---

Version 1 of the configuration document is deprecated. Use version 2, which declares forges and names them from
repository and work-source entries. The API continues to export version 1 by default; request `?version=2`, or use
`blizzard hub config export` for the new shape.
