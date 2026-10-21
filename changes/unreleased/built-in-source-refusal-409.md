---
kind: changed
surfaces: [http-api]
---

Unlinking the built-in `hub` work source from a project answers `409`, like every other write on it, where it answered
`422`. A non-membership role granted to a user that does not exist answers `404`, where it answered `422`.
