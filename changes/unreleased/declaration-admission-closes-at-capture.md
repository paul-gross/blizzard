---
kind: fixed
surfaces: [http-api]
---

A worker's asset or commit declaration that arrives after the step's outcome is captured, or between the outcome's
acknowledgement and its closure, is now refused with `409` instead of being accepted and never submitted. A declaration
made after the worker exits but before the verdict is collected now always rides the completion.
