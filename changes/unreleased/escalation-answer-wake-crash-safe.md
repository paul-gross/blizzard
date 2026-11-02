---
kind: fixed
---

A runner killed just after it closed an escalated attempt could leave the chunk stuck with no escalation at the hub,
because the escalation was written after the closure. The closure and its escalation are now written together, so a
needs-human chunk always reaches the hub. A runner killed while it resumed a session with an answer could also wake that
session a second time for the same answer; the answer is now marked consumed together with the resumed worker.
