---
kind: security
---

`blizzard dev check-invariants` no longer prints a daemon's malformed answer or a URL's credentials in its diagnostics.
It names the hub or runner host by its `scheme://host:port` and reports only that the answer was unreadable.
