---
kind: changed
surfaces: [cli]
---

The hub now checks a `review-finding-delta` at staging time whenever the chunk's graph has a `record-findings` node,
and `artifact create` no longer refuses review deltas by name on its own. A new CLI therefore stages a malformed review
delta unchecked when the hub gives no verdict, while an older CLI may still refuse by name. Staging is a local save,
not acceptance: delivery still judges the content.
