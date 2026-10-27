---
kind: deprecated
surfaces: [http-api]
removes-in: "0.4"
---

The forge address fields of repositories and work sources are deprecated: `forge_api_url` on a repository, and
`api_base` and `web_base` on a work source, on the create and edit requests that accept them, the configuration
document's entries, and the responses that return them. Name the forge with `forge` — a forge's id or slug — and read
`forge_id`, `forge_slug` and `forge_name`. Until they are removed, a write that sends one is resolved to the tenant's
forge for that origin, made when none holds it, and answers a `Deprecation` header and a `warnings` entry.
