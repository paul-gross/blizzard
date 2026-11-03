---
kind: fixed
surfaces: [http-api, event-stream, board]
---

A chunk stopped, completed, or restarted just as the hub was submitting it to its flows no longer keeps an open
submission there. The hub now writes the submission only while the chunk still stands at that node and epoch, so a
chunk's flow submissions, and the `flow-changed` frames they publish, no longer show a `submitted` fact recorded after
the lever, followed later by a second `withdrawn` one.
