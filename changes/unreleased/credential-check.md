---
kind: added
surfaces: [http-api, wire, cli, board, event-stream]
---

A credential can be checked read-only against the repositories and work sources acting with it:
`POST /api/credentials/{credential}/checks`, `blizzard hub credential check <credential> [--repo R]... [--source W]...`,
and a Check action on the board's credential, repository and work-source details. The check makes only reads, and an App
installation's token mint, and answers each operation `proven`, `granted`, `unproven`, `refused` or `unsupported`,
within 60 seconds; a use it did not reach in time is marked so, and `unreached_reason` says why no use was reached when
the forge does not answer for an App installation. The CLI exits 1 when anything is `refused` or `unsupported`, or when
no use was reached. A check naming a record the credential does not act for is refused `credential_not_used_by`. Each
check records one `credential-check` event counting its outcomes, and stores nothing else. A `forge-auth` escalation's
detail now ends with the commands that check its credential for the repository and requeue the chunk once the credential
is fixed.
