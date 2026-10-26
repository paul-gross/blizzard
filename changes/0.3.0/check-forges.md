---
kind: added
surfaces: [cli]
---

`blizzard hub config check-forges` reads a hub's store directly and names every repository and work source whose address
the coming forge migration would refuse, with the reason. It exits 1 when it names any, and the hub's startup log
carries a warning for each. Run it from the build you are about to install against the store your current hub serves.
