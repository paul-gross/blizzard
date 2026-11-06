---
kind: added
surfaces: [http-api, wire, board]
flag: cadences
---

Shows cadences on the board. A cadence's view gains `next_moments`, its schedule's next five moments after the read,
earliest first; it is empty when the cadence has no schedule or is retired. The board's Gardening tab gains Cadences: a
list and a detail page for each cadence, where a cadence is created, edited, retired, and enabled and its chores are
added, edited, and removed. A routine's detail names the cadences that keep it, and a run's detail names the cadence and
chore that minted it.
