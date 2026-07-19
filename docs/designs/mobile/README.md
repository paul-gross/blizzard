# Mobile mockups — adaptive shells over shared guts

Static HTML mockup sheets for the mobile variant of the hub/runner web dashboards.
Open either file directly in a browser; each renders phone frames beside annotation
cards that name the flow decisions and the existing `fleet` components each screen
reuses versus what is net-new.

The architectural direction the sheets assume: **one app per surface, adaptive
shells over shared guts** — no separate mobile app, no whole-tree fork at the root,
no pure-CSS reflow of the desktop board. A `ViewportService` (CDK
`BreakpointObserver` + manual override) drives page-level shell selection via
`@defer`; leaf components, queries, the SSE spine, and the design tokens
(`web/projects/fleet/src/lib/design/tokens.css`) are shared verbatim.

| Sheet | Flows |
|-------|-------|
| [core-flows.html](./core-flows.html) | Answer an agent ask from a notification · thirty-second glance board · show-off run story + turn-by-turn transcript |
| [companion-flows.html](./companion-flows.html) | Overnight digest briefing · ingest an issue from the couch · spend-alarm kill switch with slide-to-confirm · delivery approval gate · runner on-call triage |

Cross-sheet scoping notes, in build order: the push-notification channel comes
first (four of the eight flows are notification-born), then the shells; the only
genuinely new backend plumbing is a windowed digest query, the spend/redo alarm
rule, a forge diffstat read, a runner-transcript proxy route for the hub, and the
heartbeat-recovery notification. Graphs authoring stays desktop-only.
