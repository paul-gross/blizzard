---
kind: fixed
---

The `land` action re-runs a failing check inherited from the base branch through the GitHub Actions job re-run route. It
asked through the check-run re-request route, which GitHub answers only for the GitHub App that owns the check, so every
re-run was refused and an inherited failure routed as the change's own failure. A check that is not a GitHub Actions job
still reads as one that could not be re-run.

## Upgrade

Grant the forge token **Actions: Read & write** on each repository. Without it the re-run is still refused and the
failure routes as the change's own.
