---
kind: fixed
surfaces: [http-api, board]
---

Two reorders of the ready queue or the backlog at once no longer undo each other: each move, whole-order replace, and
promote or demote that names a place now lands on the order as it stands when it commits. A promote or demote that is
refused leaves every other chunk's position as it was.
