# The board

The hub's board lays a tenant's chunks out by lane and names each chunk by the work it carries. The project lens that
narrows it to one project is [projects.md](./projects.md#the-boards-project-lens)'s.

## Board and list layouts

The switch in the board's header picks a layout. **Board** is the lane grid. **List** reads every chunk as one
full-width row, top to bottom: needs human, waiting, and running, each lane newest first; then ready and backlog in the
hub's dispatch and backlog order, split by a `▲ Ready · Backlog ▼` line; then done, newest finished first. Each row
leads with an icon for its lane — paused and stopped have icons of their own — then the chunk's id over its work items,
one cell naming its runner, its step and its open PRs, and its cost. On the All lens of a tenant with more than one
project, each card and row takes its project's color as its background; under one project's lens, or on a tenant with
one project, every chunk is the same project and the board drops the color. PRs show by number alone, a blocked chunk's
marking is plain text, and a finished row closes on a check with the time it completed or stopped.

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
itself is first loading, each lane and the list fill with placeholder cards and rows. Each batch is one request to the
hub's bulk read of work items ([work-sources.md](./work-sources.md)), which asks each work source once however many of
the batch's items it holds, and the board reuses what it read for five minutes, across live updates and reconnects
alike. For a GitHub source a batch costs one GraphQL request, so loading the board spends the source's rate limit in
proportion to the batches on it not read in the last five minutes, not to its items.

## Place lines

A delivering card, and its row in the list, says where the chunk stands on each flow it is submitted to:
`landing on blizzard/master` while its turn is open, `2nd on blizzard/master` while it waits, and
`passed over on blizzard/master` while it is paused or needs a person. A chunk that has landed, or holds no flow, shows
no place line. The Delivery tab lists every chunk delivering or recently delivered, with each one's delivery history.

## The chunk dock

Clicking a card or row opens the chunk in the dock beside the lanes, and scrolls the card into view if the dock covered
it. The dock's ✕, or Escape, closes it; Escape is left alone while a field has the focus or an open menu or dialog takes
it first. With `chunk:control`, the dock's ⋮ menu carries the chunk's actions. The dock boxes the chunk's Delivery above
its Work item; the Work item box names the chunk's session efficiency and, beside it, the chunks it depends on and the
chunks it blocks, each led by its lane's icon.

The dock's Node history lists the chunk's steps oldest first. Each row closes on a usage row naming the step's session
efficiency and who ran it as `host/runner · harness`; a step sent back carries its reason on a row of its own.

## Session efficiency

The board reads a chunk's, a step's, or the fleet's usage as one figure, `$0.30 · 220kI / 32.7kO × 2.57%`: the cost,
then the distinct input (input plus cache writes, cache reads excluded), the output, and the cache miss — distinct input
over distinct input plus cache reads. Each figure but the cost reads to three significant digits, with `k` and `M` for
thousands and millions; the cost reads `~$X` for an estimate and `$X+` for a partial total. With no input the miss reads
`—`, and a usage of no tokens at all reads its cost alone. Hovering the figure spells each part out.

## Fleet spend

The board's right rail opens on Fleet spend: the fleet's session efficiency figure for today and for yesterday, each day
running from local midnight. The mobile board shows the same two rows. Below it the rail stacks the runners, open
questions, gates, and the Activity feed.

## The Delivery tab

On a desktop, `/delivery` lists every chunk delivering above a divider and every chunk delivered below it, full width. A
row leads with the chunk's lane icon and when it last moved, names the chunk's work items and its current step, draws
the pipeline `PR open → in line → landing → landed` lit up to the furthest stage it reached — `in line` carries its best
position — and links each repository's PR or landed commit. A chunk standing out of line after a bounce reads that
bounce on its row as its cause, `· withdrawn` when its submission was withdrawn right after the bounced turn, how long
ago, and its reason; a bounced chunk with no other delivery footprint drops off the list after 24 hours. A tenant with
nothing delivering or delivered reads "Nothing delivering or delivered."

Clicking a row opens the chunk in a drawer beside the list, held in the URL's `?chunk=`; clicking it again, the drawer's
✕, or Escape closes it. The drawer lists the chunk's Repositories — each with its delivery state, branch, PRs, landed
commit and last update — and its Delivery history newest first: its facts on every flow, its bounces with their cause
and reason, and the graph transitions around them. A landing attempt whose submission was then withdrawn reads
`· withdrawn`.

On a phone, `/delivery` lists the same chunks as cards, and `/delivery/chunk/:chunkId` opens one chunk's delivery on a
screen of its own; that route resolves on a desktop too, so a shared link still opens.

## On a phone

On a phone the hub's tabs sit at the foot of the screen as Board, Delivery, Fleet, Gardening and Events; on a desktop
the header orders them Board, Delivery, Fleet, Graphs, Gardening, Events and Admin. The title bar names the project lens
— tapping it opens a picker listing All projects and each project — and today's spend. The mobile board counts the
chunks done in the last 48 hours and lists them under `Done (48h)`, each with when it finished and how long ago. The
mobile Delivery tab lists the chunks delivering and those delivered in the last 48 hours as cards, and tapping one opens
that chunk's delivery on a screen of its own.
