---
kind: fixed
surfaces: [cli]
---

`blizzard runner finding list` and `finding get` now cover every work item on a chunk, not only its first. A chunk that
folded a garden-proposal item in later used to see none of that proposal's findings; it now sees the union of the
findings behind each item's accepted proposal.
