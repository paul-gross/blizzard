---
kind: changed
surfaces: [trace]
---

A hub step's `hub exec` span now stands for each turn the hub takes on the step's flows, and its span id derives from
the turn id rather than an execution slot's. A step the hub ran before turns keeps its slot-keyed span. No request links
the span any more: the hub takes a turn on its own, and its `hub action pass` span still parents on the turn's
`hub exec` span.
