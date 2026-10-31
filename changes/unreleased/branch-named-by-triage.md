---
kind: changed
---

The packaged delivery lanes now name a chunk's branch from the project's own guidance instead of always `feat/…`. Triage
declares it as the chunk's `branch` artifact, every later node reads that declaration, and each lane's build step
refuses to commit on, push to, or declare the base branch. Run `graph sync` to pick it up.
