# The board

The hub's board lays a tenant's chunks out by lane and names each chunk by the work it carries. The project lens that
narrows it to one project is [projects.md](./projects.md#the-boards-project-lens)'s.

## Board and list layouts

The switch in the board's header picks a layout. **Board** is the lane grid. **List** reads every chunk as one
full-width row, top to bottom: needs human, waiting, and running, each lane newest first; then ready and backlog in the
hub's dispatch and backlog order, split by a `▲ Ready · Backlog ▼` line; then done, newest finished first. Each row
leads with an icon for its lane — paused and stopped have icons of their own — then the chunk's id over its work items,
one cell naming its runner, its step and its open PRs, and its cost. Each card and row takes its project's color as its
background. PRs show by number alone, a blocked chunk's marking is plain text, and a finished row closes on a check with
the time it completed or stopped.

With the `queue:reorder` permission ([tenants.md](./tenants.md)), each ready and backlog row carries a grip and drags to
a new place in its own list; with `chunk:control` as well, a row dragged above the line is promoted into the ready queue
and one dragged below it is demoted to the backlog. Either way the chunk lands right after the row above where it was
dropped, or first at the top. Only ready and backlog rows drag; a row's own controls — its links and its `+N more` fold
— still act for themselves, and a click anywhere else opens the chunk. Without the permission the rows are the same,
without the grip.

The layout lives in the URL as `?layout=list`, beside the open chunk's `?chunk=` and under either lens, so a reload or a
shared link keeps it. With no `layout` the board opens on the grid.

## Work item titles

A card names each work item as its work-ref label over its title. A chunk carrying up to three items lists every pair;
past three it shows the first two and folds the rest behind `+N more`, which opens in place without opening the chunk.
Beneath the items come a blocked chunk's `blocked by` marking, then, on a card in the running, waiting, or needs-human
lane, its open PRs or `Awaiting your merge`. Next is a row with the chunk's step on the left, beside a `gate` badge
while a decision waits on a person, and its id on the right, and last its cost on the right, with a done card's
completion time on the left. The status word shows on the step row only where it says more than the lane's heading:
paused and waiting on human, which share the waiting lane, and delivering, within running. In the list each label links
out to its item at the work source.

The titles are a second read, made after the chunk list has answered, so the lanes fill first. The board reads its
chunks in batches of 100, and each batch's cards gain their titles as that batch answers. Until then each title is a
one-line placeholder under its label; a chunk whose items failed to read keeps its labels alone. While the chunk list
itself is first loading, each lane and the list fill with placeholder cards and rows. Each chunk's items are read
through its work source as the chunk page reads them ([work-sources.md](./work-sources.md)), and the board reuses what
it read for five minutes, across live updates and reconnects alike. For a GitHub source a read costs two API requests
per item, so loading the board spends the source's rate limit in proportion to the items on it not read in the last five
minutes.

## The Delivery band

Above the lanes, the **Delivery** band shows one lane per active flow — a branch with a chunk in its line or a change in
the last 48 hours ([flows.md](./flows.md)). A lane is titled by its repository's name and branch, `blizzard · master`.
It shows the chunk landing now, then the chunks in line in arrival order, each by its id, its title once read, and its
state, with the time a polling chunk next polls. Last comes the flow's last verdict and how long ago it came. With
nothing landing and no one in line, the lane folds to that verdict alone, and once the flow has been still for 48 hours
it leaves the band. With no active flow the band is hidden. The band is the tenant's, not the lens's: under a project's
lens it still shows every chunk on the branch, so you can see whom a lensed chunk waits behind. It moves live, with no
reload, as each turn starts and ends. The glance board at phone width has no band.

Clicking a chunk in the band opens it in the dock, as a card does. Clicking a lane's title opens the flow's record in
the dock instead, newest first, with `OLDER` for the page before. The record lives in the URL as `?flow=`, in place of
`?chunk=`; one dock shows one or the other.

## Place lines

A delivering card, and its row in the list, says where the chunk stands on each flow it is submitted to:
`landing on blizzard/master` while its turn is open, `2nd on blizzard/master` while it waits, and
`passed over on blizzard/master` while it is paused or needs a person. A chunk that has landed, or holds no flow, shows
no place line. The chunk's timeline shows its submission, turns, and landing among its other steps, each linked to the
flow's record.
