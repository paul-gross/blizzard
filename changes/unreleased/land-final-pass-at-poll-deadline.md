---
kind: changed
---

A delivery that reaches its poll timeout now takes one last pass before bouncing. Checks that have turned green by then
land, and a change waiting on a person's merge is no longer bounced by that timeout.
