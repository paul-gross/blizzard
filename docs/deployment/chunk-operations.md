# Chunk operations

Moving a chunk between graphs, editing what an unclaimed chunk will run with, steering which graph a name resolves to,
declaring or releasing a dependency between chunks, and entering a parked chunk's session by hand.

## Review and landing links

The hub board card shows each open pull request by repository, with outbound links outside the card's chunk-selection
button. Select a card to see the same delivery information in the desktop detail dock; on a narrow screen, open the
chunk's routed page and use its General tab. A landed repository shows its merged commit, not the earlier working-branch
tip. Repositories land independently, so a partially landed chunk can show both a landed commit and an open PR. Delivery
lands only the commit the chunk submitted, plus base merges it makes itself, so a commit you push onto a delivering PR's
branch sends the chunk back for repair instead of landing.

An open PR alone does not mean you must merge it: automatic delivery can have a reviewable PR while CI or merge runs.
“Awaiting your merge” appears only when delivery explicitly parks for an external merge. If a repository has no known
forge web address, its landed repository and commit remain readable as text instead of a link.

## Routing

Each verb lives in the file below that owns it; a fact stated in one is linked from the others, never restated.

| File                                                    | Read when…                                                                                                             |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| [`editing.md`](./chunk-operations/editing.md)           | …changing an unclaimed chunk's pinned graph or its default model and effort, and reading back what a surface inherits. |
| [`migration.md`](./chunk-operations/migration.md)       | …aiming a chunk that has already run at another graph, or keeping a lineage on its newest mint.                        |
| [`graphs.md`](./chunk-operations/graphs.md)             | …retiring or re-enabling a graph to steer which mint a name resolves to.                                               |
| [`dependencies.md`](./chunk-operations/dependencies.md) | …declaring that one chunk depends on another, or releasing a standing dependency.                                      |
| [`takeover.md`](./chunk-operations/takeover.md)         | …entering a parked chunk's session from your own terminal, or resolving an escalation no runner can enter.             |
