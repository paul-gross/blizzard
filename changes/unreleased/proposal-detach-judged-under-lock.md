---
kind: fixed
surfaces: [http-api, cli]
---

Detaching a finding from a garden proposal while another detach of that finding is in flight is now refused as not
linked, instead of reporting success for a link that was already gone. Attaching a finding that a concurrent detach just
unlinked now succeeds instead of being refused as already linked.
