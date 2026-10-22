---
kind: changed
surfaces: [cli]
upgrade-guide: caps-count-estimated-spend
---

Both runner caps, `runner_ceiling_usd` and `chunk_cap_usd`, now trip on billed plus estimated spend — the one figure the
board and the hub CLI show. The ceiling's pause reason and the spend-cap escalation's detail render that figure with the
same `~` and `+` markers, and their `+` marks only a row carrying neither a billed nor an estimated amount.

## Upgrade

A cap set against billed spend alone can trip early on a runner spending a subscription; review each runner's caps
against its recent `~`-marked spend and raise any it would cross.
