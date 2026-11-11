---
kind: changed
surfaces: [http-api, cli]
---

A bare `blizzard runner artifact get <name>` for a name several nodes produced now returns the newest entry instead of
refusing with `409`; `--node` still narrows the read to one node's series. A name that resolves in more than one scope
is still refused, and the refusal now advises only `--scope`.
