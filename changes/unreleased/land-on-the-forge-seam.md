---
kind: changed
---

`land` now answers what a forge says about the repository's own configuration at once. A credential the forge rejects
escalates the chunk to a person with cause `forge-auth`, and a repository the forge does not know, or cannot show to
that credential, escalates with cause `forge-config`; before, both waited out the deliver node's poll timeout. A forge
that rate-limits a comparison is asked again once the limit lifts, where before the head was refused as foreign. A forge
that does not offer what `land` needs escalates the chunk with cause `forge-capability-missing` before the action runs.
A re-run of an inherited check failure that the forge refuses routes the failure rather than waiting.
