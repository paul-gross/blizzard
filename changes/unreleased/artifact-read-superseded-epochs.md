---
kind: added
surfaces: [cli, http-api]
---

A worker can read the epochs an artifact series has superseded. `blizzard runner artifact get NAME --epoch N` reads that
one epoch of a node-scope artifact, alongside `--node`; without `--epoch` it still returns the newest. An epoch the
series does not hold is refused, naming the epochs it does hold. `blizzard runner artifact list --all-epochs` lists
every epoch of each node-scope series, newest first. Graph and system scope have no epochs, so both options are refused
with them.
