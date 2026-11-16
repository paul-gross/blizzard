---
kind: changed
surfaces: [store, egress]
---

Egress cursor appends are ordered at the store, so an overlapping pass cannot move a cursor past an operator's reset.
The reset reports its actual predecessor. A pass already underway may still publish files after the reset, and later
passes may replay those rows.

## Upgrade

Run `blizzard hub migrate --dir <hub-dir>` before starting the new host. The migration adds cursor ordering and seeds
the egress lock. If it is skipped, the host refuses to start on the store revision mismatch. Rolling back retains cursor
history but restores timestamp authority for choosing the current cursor.
