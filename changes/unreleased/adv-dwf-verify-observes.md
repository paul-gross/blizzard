---
kind: changed
---

The advanced development workflow's verify step no longer commits fixes. It reports every failure in its verification
report and selects `fail`, and build repairs it. Before, a fix committed during verify reached review without a recorded
commit, so review stopped and asked an operator how to proceed.

## Upgrade

Re-mint the packaged graphs with `blizzard hub graph sync`. A chunk already pinned keeps its version.
