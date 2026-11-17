---
kind: added
---

`blizzard runner status --json` prints the machine-local view as one JSON object. A failure prints one
`{"error": {"message": …}}` object and exits with the same code as the text form.
