# Flows

A **flow** is one repository branch that work lands on. Every hub step that lands work — `deliver` on the default graph
— is a **submission** to the flow of each branch it holds, and the hub takes it as a **turn** on those flows, one at a
time per flow. A flow's **line** is its open submissions in the order they arrived. Landings on different branches run
side by side; chunks landing on one branch take turns in arrival order. The board, the CLI, and the API below read the
same line the hub drives.

## Places in the line

Each open submission holds a place on every flow it submitted to. Its state is the same on all of them:

| State         | Meaning                                                                                                           |
| ------------- | ----------------------------------------------------------------------------------------------------------------- |
| `landing`     | Its turn is open now.                                                                                             |
| `next`        | It may take a turn, and nothing that arrived before it on any of its flows may.                                   |
| `waiting`     | It may take a turn, behind one that arrived before it.                                                            |
| `polling`     | Its last turn found its checks still running; it takes no turn until its next poll is due, and says when that is. |
| `passed-over` | Its chunk is not `delivering` — paused or needing a person — so the line moves on without it until it is again.   |

Its **position** on a flow is 1 while it is `landing` or `next`. Otherwise it is 1 plus the submissions ahead of it on
that flow that may take a turn now, so a paused chunk or one waiting on a poll does not hold the others' numbers back.

## The record

A flow's **record** is every entry written about it, newest first: each submission, each turn's start and end, each
landing with the revision it left the branch at, and each withdrawal with why the submission left. A flow's **last
verdict** is its newest turn whose outcome closed its submission — `landed`, or a failure that sent the chunk back to
build. A turn that is still polling, was interrupted, or was cut off at its deadline is not a verdict. Its **last
landing** is its newest `landed` entry.

## Reading flows

`blizzard hub flow list` prints each active flow: its branch, the turn open on it, its line with each chunk's state and
position, and its last verdict and landing. A flow is active while a chunk is in its line or it changed in the last 48
hours; `--all` prints every flow. `blizzard hub flow show <flow>` prints one flow the same way. With `--record` it
prints the flow's record instead, newest first, up to `--limit` entries (50 by default). Both take `--json` for the raw
response body. `blizzard hub chunk show <chunk>` prints a line under the chunk's status for each place it holds.

The same reads over HTTP:

| Route                                     | Answers                                                                                                                              |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `GET /api/flows`                          | The tenant's flows in resource order, each with its open turn, its line, and its last verdict and landing.                           |
| `GET /api/flows?active=true`              | Only the active ones.                                                                                                                |
| `GET /api/flows/{flow_id}`                | One flow, or 404 for one the tenant does not hold.                                                                                   |
| `GET /api/flows/{flow_id}/record`         | The record newest first, up to `limit` entries (50 by default, 200 at most); pass the page's `next_before` as `before` for the next. |
| `GET /api/chunks`, `GET /api/chunks/{id}` | Each chunk's `flow_places`; the named chunk also carries its `flow_record`, every entry its submissions wrote, oldest first.         |

Flows are the tenant's, not a project's: a branch's line is shared by every project landing on it, so the flow routes
take no `?project=`. Each entry written to a record is announced on the hub's event stream as a `flow-changed` frame,
which is how the board's band and place lines move live.
