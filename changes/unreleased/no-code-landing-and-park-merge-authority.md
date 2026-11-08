---
kind: fixed
surfaces: [http-api]
---

Delivery no longer records a landing when nothing was landed: a `fast-forward` repository whose base already holds the
work now resolves as no code to land, as `merge-on-checks` does, and every policy proves that with the same strict
check of the base and the live head. A `park` repository keeps its external merge authority: a person's merge of the
parked pull request finishes the repository at the revision the merge produced, even when the pull request's head
changed, and its reference notes no longer delay that record.
