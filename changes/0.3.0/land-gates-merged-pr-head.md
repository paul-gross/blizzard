---
kind: fixed
---

A chunk whose pull request was already merged before `land` ran is no longer counted as landed while that merged head
has red checks. `land` gates the merged head first, as it gates any other.
