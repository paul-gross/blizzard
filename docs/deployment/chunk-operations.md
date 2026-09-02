# Chunk operations

Moving a chunk between graphs, editing what an unclaimed chunk will run with, steering which graph a name resolves to,
declaring or releasing a dependency between chunks, and entering a parked chunk's session by hand.

## Routing

Each verb lives in the file below that owns it; a fact stated in one is linked from the others, never restated.

| File                                                    | Read when…                                                                                                             |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| [`editing.md`](./chunk-operations/editing.md)           | …changing an unclaimed chunk's pinned graph or its default model and effort, and reading back what a surface inherits. |
| [`migration.md`](./chunk-operations/migration.md)       | …aiming a chunk that has already run at another graph, or keeping a lineage on its newest mint.                        |
| [`graphs.md`](./chunk-operations/graphs.md)             | …retiring or re-enabling a graph to steer which mint a name resolves to.                                               |
| [`dependencies.md`](./chunk-operations/dependencies.md) | …declaring that one chunk depends on another, or releasing a standing dependency.                                      |
| [`takeover.md`](./chunk-operations/takeover.md)         | …entering a parked chunk's session from your own terminal, or resolving an escalation no runner can enter.             |
