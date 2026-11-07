---
kind: added
surfaces: [http-api]
---

Selftest runs (`POST /api/selftests`, `GET /api/selftests/{id}`) now report `settled`: `false` while the run is still
going, `true` once it has concluded. Clients read `settled` to stop polling instead of comparing `status`, which is now
published as the named `SelfTestStatus` vocabulary.
