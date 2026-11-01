---
kind: fixed
---

`land` no longer asks for the one-time re-run of an inherited CI failure while another check on the change is still
running; it waits for the run to finish. A re-run the forge refuses because the run is still going now waits and is
asked again, and only a refusal for permissions or for a check that is not an Actions job routes as the change's own
failure. Every refused re-run is written to the `land` log with the forge's status and message.
