# The board

The hub's board lays a tenant's chunks out by lane and names each chunk by the work it carries. The project lens that
narrows it to one project is [projects.md](./projects.md#the-boards-project-lens)'s.

## Board and list layouts

The switch in the board's header picks a layout. **Board** is the lane grid. **List** reads the same lanes top to bottom
as collapsible sections, in this order: needs human, waiting, running, ready, backlog, and done. Done starts folded.
Each section keeps its lane's own order, so ready and backlog still read in the hub's dispatch and backlog order. A row
puts the chunk's work, project, step, runner, open PRs, cost and id in aligned columns. PRs show by number alone, and a
blocked chunk's marking is plain text there, so a row selects its own chunk only.

The layout lives in the URL as `?layout=list`, beside the open chunk's `?chunk=` and under either lens, so a reload or a
shared link keeps it. With no `layout` the board opens on the grid.

## Work item titles

A card leads with its work items' titles. A chunk carrying up to three items lists every title; past three it shows the
first two and folds the rest behind `+N more`, which opens in place without opening the chunk. Beneath the titles come a
blocked chunk's `blocked by` marking, then, on a card in the running, waiting, or needs-human lane, its open PRs or
`Awaiting your merge`. Next is a row with the chunk's step on the left, beside a `gate` badge while a decision waits on
a person, and its id on the right, and last its cost on the right, beside the completion time on a done card. The status
word shows on the step row only where it says more than the lane's heading: paused and waiting on human, which share the
waiting lane, and delivering, within running. In the list, a chunk with one work item also shows the first line of that
item's description.

The titles are a second read, made after the chunk list has answered, so the lanes fill first. The board reads its
chunks in batches of 100, and each batch's cards gain their titles as that batch answers. Until then a card shows its
work-ref labels in their place, as does a chunk whose items failed to read. Each chunk's items are read through its work
source as the chunk page reads them ([work-sources.md](./work-sources.md)), and the board reuses what it read for five
minutes, across live updates and reconnects alike. For a GitHub source a read costs two API requests per item, so
loading the board spends the source's rate limit in proportion to the items on it not read in the last five minutes.
