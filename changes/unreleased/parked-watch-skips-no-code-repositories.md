---
kind: fixed
---

A parked pull request already in review is now polled, and its merge recorded, even when a repository after it in the
landing order is not yet ready (missing capability, unavailable secret, pending checks) and an earlier repository needed
no merge.
