---
kind: fixed
surfaces: [store, cli]
---

A runner crash during `blizzard runner takeover` no longer strands the chunk. On restart the runner finishes the
displacement the entry began and ends that entry, so the next `takeover` is admitted instead of refused as already open.
A lease's mint, owner, token and fence fact are likewise now written as one transaction, so a crash cannot leave a lease
that is missing any of them.
