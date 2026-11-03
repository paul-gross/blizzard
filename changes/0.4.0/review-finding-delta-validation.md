---
kind: changed
surfaces: [cli]
---

`blizzard runner artifact create --name review-finding-delta` now checks the body against the review round's delta
format and refuses prose, `none`, or a malformed shape with a field-level error, so a reviewer corrects it in the same
turn rather than at the hub's later parse. The three development graphs' review prompts now document the asset's format.
