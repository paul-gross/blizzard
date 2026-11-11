---
kind: fixed
surfaces: [cli]
---

Runner CLI verbs report an unreadable runner answer with the verb and the door reached, without exposing the answer's
contents, instead of failing with a decoding or validation error.

## Upgrade

If a verb reports an unreadable answer, restart the runner host on the same build as the CLI. Before retrying a write,
check whether it already took effect.
