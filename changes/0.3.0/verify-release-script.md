---
kind: added
surfaces: [cli]
---

`scripts/verify-release.sh <tag>` verifies a published release from outside: the image's signature and attestations, the
attached files, and a fresh install from the release page, needing only `gh`, `cosign`, and `uv`. It refuses a release
cut before `v0.2.0-rc.2`.
