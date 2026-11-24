---
kind: fixed
surfaces: [http-api]
---

Commit pointers that name one repository with different owner case, such as `Acme/app` and `acme/app`, are now one
repository at delivery, so their disagreeing commits are refused instead of both landing.
