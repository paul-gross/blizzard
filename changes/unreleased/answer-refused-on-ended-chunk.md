---
kind: fixed
surfaces: [http-api, board]
---

An answer to a question whose chunk is stopped or finishes while the answer is being sent is now refused with `409`, as
it is once the chunk has ended, instead of landing as the question's answer on the ended chunk.
