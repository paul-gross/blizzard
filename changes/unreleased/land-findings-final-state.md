---
kind: fixed
---

After a delivery, `delivery-findings` and `hub-log.land` could still list checks as running and say the hub was
re-polling, because they described an earlier poll of the landing. They are now written once, as the landing visit ends,
with each check in the state it was in at that point.
