---
kind: added
surfaces: [http-api, wire]
---

The hub now answers questions the board used to work out itself. Runner project entries carry `claimable`, whether the
runner can claim the project now. Gate choices carry `would_override_red_checks`, whether resolving to the choice would
override red checks. A refused sign-in and a refused project both come back as named, typed refusals.
