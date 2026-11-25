---
kind: fixed
surfaces: [board]
---

A garden redelivery whose proposal names one finding twice, by ref and by id, is now refused and bounced, instead of
leaving the chunk stuck in `delivering` while the hub retries it. A delivery that names one finding in two delta
artifacts is now refused too.
