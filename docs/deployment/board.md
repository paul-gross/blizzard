# The board

The hub's board lays a tenant's chunks out by lane and names each chunk by the work it carries. The project lens that
narrows it to one project is [projects.md](./projects.md#the-boards-project-lens)'s.

## Board, list and tracks layouts

The switch in the board's header picks a layout. **Board** is the lane grid, combining needs-human and waiting chunks in
one **Human Attention** column. **List** keeps those lanes separate and reads every chunk as one full-width row, top to
bottom: needs human, waiting, and running, each lane newest first; then ready and backlog in the hub's dispatch and
backlog order, split by a `▲ Ready · Backlog ▼` line; then done, newest finished first. Each row leads with an icon for
its lane — paused and stopped have icons of their own — then the chunk's id over its work items, one cell naming its
runner, its step and its open PRs, and its cost. On the All lens of a tenant with more than one project, each card and
row takes its project's color as its background; under one project's lens, or on a tenant with one project, every chunk
is the same project and the board drops the color. PRs show by number alone, a blocked chunk's marking is plain text,
and a finished row closes on a check with the time it completed or stopped.

With the `queue:reorder` permission ([tenants.md](./tenants.md)), each ready and backlog row carries a grip and drags to
a new place in its own list; with `chunk:control` as well, a row dragged above the line is promoted into the ready queue
and one dragged below it is demoted to the backlog. Either way the chunk lands right after the row above where it was
dropped, or first at the top. Only ready and backlog rows drag; a row's own controls — its links and its `+N more` fold
— still act for themselves, and a click anywhere else opens the chunk. Without the permission the rows are the same,
without the grip.

**Tracks** lays the fleet out in time: one track per environment, grouped under its workspace and the workspace under
its runner host, with every runner on the host named beside it. Chips pick how far back the window reaches — 6 hours, 24
hours, 3 days or 7 days — and while it ends at now the bar reads `following now` and the window moves with the clock.
Panning or zooming pins it where it lands, and `pinned · back to now` returns it to following now.

Each block on a track is a node step a chunk spent in that environment, from when it reached the node to when it left —
a step still running reaches now. Zoomed out far enough that a chunk's consecutive steps would be too narrow to see,
they fold into one block spanning them, which names how many steps it holds; zoom in to see each. A block is named by
its node and shaded by it. A step that failed is cut by darker diagonal stripes over its shade; a step still running
carries a green edge at its head, red while an escalation of it still waits on a person. An escalation something later
superseded leaves no mark on the block, though its hover still names it. Under the all-projects lens with more than one
project, each block is tinted with its chunk's project colour; otherwise it is tinted with its runner's colour, and
while the runners are few the bar keys each runner's name to its colour. A chunk moved to other environments mid-step
shows the step on each environment that held it. Beside the timeline, each environment reads what it holds now: the
chunk and step it is running — halted, with the cause, while an escalation of the step still waits on a person — or the
one it last went idle after. An environment shows while it holds a step in the window, so one retired since still shows
the work it held.

Selecting a block opens the step drawer. It names where the step ran — the host, workspace, environment and runner —
when it began and ended and how long it held, its spend with tokens, and the harness and model its session ran on. It
lists the step's escalations, the artifacts the node recorded during the step, and the session's transcript, which keeps
growing while the step runs. Last comes the chunk's path: every step it took, across every environment, on one strip,
with the waits between them; selecting a step there moves the drawer to it.

The layout lives in the URL as `?layout=list` or `?layout=tracks`, beside the open chunk's `?chunk=` and under either
lens, so a reload or a shared link keeps it. With no `layout` the board opens on the grid. Under Tracks the open step
lives there too: `?step=` names it by its session, and `?at=` by the instant it began, for a step no session of a runner
ran. Selecting a different chunk clears both. Switching project lenses keeps the selected layout but clears the open
chunk and step.

## Urgency on the board

Cards, list rows and phone rows show a colored urgency pill on unfinished work when its urgency is not the tenant's
default. The pill shows its emoji, icon, or name. Finished work and work at the default have no pill in the lanes. The
chunk detail header names the urgency even at the default; with `chunk:control`, its control and the card's actions can
change an unfinished chunk to another live urgency. The board still displays ready work in rank order, not claim order.

The tenant's urgency set and default are managed under **Admin → Urgencies**. The new hub item, garden proposal accept,
routine run, routine and cadence chore forms also offer urgency choices. Their defaults and how urgency affects a
runner's next claim are described in [urgencies.md](./urgencies.md#on-the-board).

On the Fleet tab, each runner row in a host's detail, including a hostless runner's detail, has a **Next** line. It
links the chunk the hub expects that runner to reach for, with its project and urgency, or says `Nothing claimable`.
Expand **Passed** to see the chunks it steps past and why, and what a per-project hold would reach or stop at. This is a
snapshot, not a promise; its refresh and the walk behind it are explained in
[urgencies.md](./urgencies.md#a-runners-next-claim).

## Work item titles

A card names each work item as its work-ref label over its title. A chunk carrying up to three items lists every pair;
past three it shows the first two and folds the rest behind `+N more`, which opens in place without opening the chunk.
Beneath the items come a blocked chunk's `blocked by` marking, then, on a card in the Running or Human Attention column,
its open PRs or `Awaiting your merge`. Next is a row with the chunk's step on the left, beside a `gate` badge while a
decision waits on a person, and its id on the right, and last its cost on the right, with a done card's completion time
on the left. The step row names paused and waiting-on-human states within Human Attention, and delivering within
Running. In the list each label links out to its item at the work source.

A card in Running or Human Attention, or a list row in the running, waiting, or needs-human lane, also carries the
chunk's graph-progress strip: one block per node of its graph, in tens, each block reading its node's state — current in
pulsing cyan, passed in green, failed in red, and unvisited left dark. Hovering a block names the node and its state. A
finished chunk carries no strip; its `graph_progress` is empty.

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

The dock's Node history lists the chunk's steps oldest first, grouping consecutive steps under graph headings that link
to their graphs. Where usage is recorded, a step's usage row names its dollar cost and who ran it as
`host/runner · harness`; the full chunk page's Node history keeps the richer session efficiency figure. A bounce shows
its repository and PR identity when available, with its reason on a separate row beneath; without that identity the
reason appears directly in the verdict column. Hovering the bounce shows its full diagnostic text.

## Session efficiency

The board reads a chunk's, a step's, or the fleet's usage as one figure, `$0.30 · 220kI / 32.7kO × 2.57%`: the cost,
then the distinct input (input plus cache writes, cache reads excluded), the output, and the cache miss — distinct input
over distinct input plus cache reads. The hub dock's Node history shows only the dollar cost alongside the runner and
harness, rather than the token and cache figures. Each figure but the cost reads to three significant digits, with `k`
and `M` for thousands and millions; the cost reads `~$X` for an estimate and `$X+` for a partial total. With no input
the miss reads `—`, and a usage of no tokens at all reads its cost alone. Hovering the figure spells each part out.

## Fleet spend

The board's right rail opens on Fleet spend: the fleet's session efficiency figure for today and for yesterday, each day
running from local midnight. The mobile board shows the same two rows. Below it the rail stacks the runners, open
questions, gates, and the Activity feed; empty Questions and Gates panels are hidden.

## The Delivery tab

On a desktop, `/delivery` lists every chunk delivering above a divider and every chunk delivered below it, full width. A
row leads with the chunk's lane icon and when it last moved, names the chunk's work items and its current step, draws
the pipeline `PR open → in line → landing → landed` lit up to the furthest stage it reached — `in line` carries its best
position — and links each repository's PR or landed commit. A chunk standing out of line after a bounce shows a compact
repository and PR identity when available, followed by its reason. Hovering that bounce shows the original diagnostic
text, a `withdrawn` marker if its submission was withdrawn right after the bounced turn, and the absolute time; a
bounced chunk with no other delivery footprint drops off the list after 24 hours. A tenant with nothing delivering or
delivered reads "Nothing delivering or delivered."

Clicking a row opens the chunk in a drawer beside the list, held in the URL's `?chunk=`; clicking it again, the drawer's
✕, or Escape closes it. The drawer lists the chunk's Repositories — each with its delivery state, branch, PRs, landed
commit and last update — and its Delivery history newest first: its facts on every flow, its bounces with compact
repository and PR identities and reasons, and the graph transitions around them. A bounce's full diagnostic text is
available on hover. A landing attempt whose submission was then withdrawn reads `· withdrawn`.

On a phone, `/delivery` lists the same chunks as cards, and `/delivery/chunk/:chunkId` opens one chunk's delivery on a
screen of its own; that route resolves on a desktop too, so a shared link still opens.

## The Gardening tab

`/gardening` shows a project's gardening records under one strip of tabs: Scopes, Routines, Cadences, Runs, Findings and
Proposals. Each opens a list beside the selected record's detail.

**Cadences** lists the project's cadences at `/p/{project}/gardening/cadences`. A row names the cadence, its rhythm —
its cron and zone, then its spacing, as in `0 20 * * * America/Chicago, at least 10 days apart` — and when its next
moment comes, how many chores it keeps, its errands counted by state (`due`, `running`, `waiting`, `stopped`), and an
`attention` badge counting what stops them. A retired cadence reads `retired`. While the `cadences` flag is off the list
opens with a line saying cadences are kept and editable but mint no run until it is turned on. A person who may edit
graphs creates a cadence from **New cadence**: a slug, a name, a cron and zone, a spacing and its anchor.

Selecting a cadence opens its detail at `/p/{project}/gardening/cadences/{slug}`: whether it is enabled or retired, its
rhythm, its next five moments, what needs attention, and each chore — the routine it runs, linked, the scopes it runs
over, its mode, whether it promotes — with a table of its errands: the scope, the errand's state and why it is stopped,
its last run, linked to that run, and when it is next due. The detail's actions are **Edit**, **Retire** or
**Re-enable**, and each chore's **Edit** and **Remove** and the cadence's **Add chore**; retiring, re-enabling and
removing a chore ask first. Each edit carries the revision the detail read, so one made against a cadence someone else
has since changed is refused and says so rather than overwriting it.

A routine's detail names what keeps it under **Kept by**: each chore that runs it on an enabled cadence of its project,
with the cadence linked, the chore's key and its next moment. A routine no cadence keeps reads "No cadence keeps it — it
runs when started." and its row in the routines list reads `manual` rather than `cadence`. A run a cadence minted names
it on the run's detail — `Kept by` the cadence, linked, and the chore — or that its chore was removed since.

## On a phone

On a phone the hub's tabs sit at the foot of the screen as Board, Delivery, Fleet, Gardening and Events; on a desktop
the header orders them Board, Delivery, Fleet, Graphs, Gardening, Events and Admin. The title bar names the project lens
— tapping it opens a picker listing All projects and each project — and today's spend. The mobile board counts and lists
the server-selected done chunks, including the newest floor as well as those inside the window
([observability.md](./observability.md#list-pagination)), each with when it finished and how long ago. The mobile
Delivery tab lists the chunks delivering and those delivered in the last 48 hours as cards, and tapping one opens that
chunk's delivery on a screen of its own.
