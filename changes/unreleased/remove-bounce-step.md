---
kind: removed
surfaces: [http-api]
retires: deprecate-bounce-step
breaking: true
upgrade-guide: runners-below-04-are-refused
---

A `bounce-envelope` artifact no longer carries `step`, which the hub has written only as `null` since 0.3: a bounce
names the `action` whose pass answered it instead. The board reads an envelope stored before 0.3 by its `cause` and
`detail`, ignoring the `step` it carries, and no longer draws its Step and Output rows.

## Upgrade

Read a bounce's `action` rather than its `step`. A client that reads `step` finds the key absent on every envelope this
release writes; an envelope stored earlier keeps the key in its stored body.
