---
kind: changed
surfaces: [board]
---

Delivery labels structured failure reasons as merge conflict, existing check failures, or timed out; a reason with no
wording reads as itself and a failure with no reason reads delivery failed. A repository failure with no precise reason
never inherits a timeout from another attempt.
