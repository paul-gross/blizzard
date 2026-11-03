---
kind: fixed
surfaces: [http-api, board]
---

A resolution of a gate decision whose chunk is stopped or restarted while the resolution is being sent is now refused
with `409`, as it is once the gate has closed, instead of landing as the gate's resolution on the closed gate.
