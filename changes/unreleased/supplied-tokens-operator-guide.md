---
kind: added
surfaces: [cli, http-api]
---

`docs/deployment/credentials.md` is the operator's guide to credentials: creating a `github.token` credential with
`blizzard hub credential`, the permissions its token needs and what a fine-grained token cannot do, the credentials an
upgrade gives existing repositories and work sources with no token re-entered, rebinding a record with `--credential`,
and each refusal. The repository and work source examples now use `--credential`.
