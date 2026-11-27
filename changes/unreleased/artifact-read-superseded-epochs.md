---
kind: added
surfaces: [cli, http-api]
---

A worker can read the earlier entries of a node's artifact series: `blizzard runner artifact list --all-epochs` lists
every one, and `blizzard runner artifact get NAME --epoch N` reads the one listed under that number, while a read
without it still returns the newest. A number the series does not hold is refused, naming those it holds, and both
options are refused for graph and system scope.
