// @ts-check
/*
 * The shell sweep (issue #171) — the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method.
 *
 * A real, headless-Chromium proof of layout claims jsdom (this repo's default
 * unit-test environment) cannot make good on: it parses `@container` rules
 * but never evaluates them, and it never actually lays out or clamps text.
 * These specs run instead under `@angular/build:unit-test`'s real-browser
 * mode (`--browsers=ChromiumHeadless`, backed by `@vitest/browser-playwright`),
 * where layout, `@container` collapse, line-clamping, and hit-testing are all
 * genuine.
 *
 * Named `*.shell-sweep.spec.ts` and excluded from each project's default
 * `ng test` run (`angular.json`'s per-project `test.exclude`) because they
 * need that real browser rather than jsdom — this script is the one
 * documented way to run them:
 *
 *   npm run shell-sweep   (from web/)
 *
 * The specs:
 *   - projects/hub/src/app/nav/app-nav-menu.shell-sweep.spec.ts — the hub
 *     board shell (BoardHeader + AppNavMenu), swept over width only (no
 *     username is ever shown there): never lets the profile menu drift
 *     off-viewport as the window narrows.
 *   - projects/hub/src/app/board/chunk/chunk-page-layout.shell-sweep.spec.ts —
 *     the chunk detail page's General tab (`ChunkGeneralTab`, blizzard#203):
 *     work item, issues and node history genuinely stack at phone widths, and
 *     node history genuinely sits beside a shared work-item/issues column at
 *     1024px — the `@media (min-width: 720px)` grid split jsdom cannot
 *     evaluate — plus the same page's takeover panel (blizzard#251) and its
 *     Transcripts tab (blizzard#248), the latter both standalone and through
 *     its real ChunkPage → container → tab chain.
 *   - projects/hub/src/app/board/chunk/chunk-artifacts-tab-layout.shell-sweep.spec.ts —
 *     the Artifacts tab's real `ChunkPage` → `ChunkArtifactsTab` →
 *     `ChunkArtifactsPanel` chain (review M1): a 40-artifact nav list genuinely
 *     scrolls inside a bounded box rather than clipping with no scroll
 *     container — the `height: 100%` percentage chain jsdom cannot resolve.
 *   - projects/runner/src/app/nav/app-header.shell-sweep.spec.ts — the
 *     runner app root's own desktop header (`AppHeader`, moved out of
 *     `LocalPanelLayout` by issue #325), swept over width × signed-in
 *     username length — the axis issue #163's actual defect lived on: same
 *     profile-menu-drift proof.
 *   - projects/local-panel/src/lib/local-panel-mobile.shell-sweep.spec.ts —
 *     the runner's mobile chunk list (`LocalPanelMobile` → `ChunkCard`,
 *     issue #176): a five-work-item chunk card's per-line
 *     `-webkit-line-clamp: 2` lines genuinely stack (distinct
 *     `getBoundingClientRect().top`s) with no horizontal overflow, at the
 *     narrow phone widths this component — unlike `ChunkRow` — is actually
 *     reached at, beneath the persistent mobile bottom tab bar.
 *   - projects/fleet/src/lib/runners/runner-view.shell-sweep.spec.ts — the
 *     runner registry's rate-limit pace bars (issue #218): the stacked
 *     utilization/elapsed pair per sampled window genuinely stacks, with no
 *     horizontal overflow, at the board right rail's ~390px width.
 *   - projects/local-panel/src/lib/transcript-panel.shell-sweep.spec.ts —
 *     the transcript panel's two new blizzard#249 states, reachable from the
 *     mobile chunk-detail screen (`data-testid="detail-transcript"`): the
 *     archived badge + truncation banner render with no horizontal overflow,
 *     and the hub-unreachable degrade banner wraps rather than pushing past
 *     the viewport under `nowrap`.
 *   - projects/local-panel/src/lib/session-recovery-view.shell-sweep.spec.ts —
 *     the runner's session-recovery surface (blizzard#312), which replaces the
 *     whole panel while a bounce could not be silently completed: the
 *     headline, detail copy, and retry control hold their layout with no
 *     horizontal overflow at phone widths.
 *   - projects/runner/src/app/nav/app-nav.shell-sweep.spec.ts — the runner
 *     shell's own top tab strip (issue #313, `AppNav`): the Board/Events
 *     labels never force the strip to overflow its own width.
 *   - projects/runner/src/app/board/chunk/chunk-detail-page.shell-sweep.spec.ts —
 *     the runner-local chunk detail page (issue #318): each of its three
 *     tabs — General, Artifacts, Transcripts — genuinely stacks its own
 *     sections with no horizontal overflow at phone widths, including the
 *     General tab's `@media (min-width: 720px)` two-column grid collapse and
 *     a long unbroken artifact key on the Artifacts tab.
 *   - projects/fleet/src/lib/design/hover-tint.shell-sweep.spec.ts — the
 *     shared `--tint-hover`/`--tint-selected` wash on board-card,
 *     chunk-timeline, and chunk-artifacts rows: a computed-style claim, not a
 *     layout one — jsdom parses a `:hover` rule without ever evaluating it,
 *     so only a real pointer (Playwright's `userEvent.hover`) proves a
 *     hovered row differs from both its resting and its selected state.
 *   - projects/fleet/src/lib/chunk-detail/chunk-facts-alignment.shell-sweep.spec.ts —
 *     the work item panel's two fact tables (`ChunkFacts` + `ChunkTokenBreakdown`,
 *     `--kv-label-col`/`--chunk-facts-pad`): their value columns genuinely land
 *     at the same horizontal position under a long runner identity that wraps
 *     — a real CSS grid layout claim jsdom cannot make.
 *   - projects/fleet/src/lib/board-card/board-card-control-row.shell-sweep.spec.ts —
 *     `BoardCardComponent`'s control row (D8, issue #364): PROMOTE and DELETE
 *     genuinely sit side by side with no overlap or overflow at the board right
 *     rail's narrow widths — a real CSS flex-row layout claim jsdom cannot make.
 *   - projects/fleet/src/lib/graphs/graph-detail.shell-sweep.spec.ts — the
 *     graphs container/presentational split's two Phase-2 children
 *     (`GraphDetailHeader`, `GraphDetailEdges`): the header's identity row,
 *     lifecycle actions, error line, and entry line genuinely stack with the
 *     real gap `:host`'s flex column has to reproduce now that they moved out
 *     from under `.body`'s own flex column, and the edges section's per-node
 *     blocks and prompt addendum genuinely stack too — real CSS layout claims
 *     jsdom cannot make.
 *   - projects/fleet/src/lib/garden/routine-panel.shell-sweep.spec.ts — the
 *     gardening routine panel (blizzard#397): the record, strategy, trend,
 *     measurement, and last-swept blocks genuinely stack at 1280/390/320px
 *     with no horizontal overflow, and the last-swept table's own long
 *     revision hashes wrap inside their column rather than widening it.
 *   - projects/fleet/src/lib/garden/garden-runs.shell-sweep.spec.ts — the
 *     gardening runs-and-findings tab's two presentational components
 *     (blizzard#401 Phase 3): `FleetRunList`'s escalated row carries a
 *     genuinely different computed background/border-left color from a
 *     normal row at 390px, and `FleetRunDelta` genuinely stacks its
 *     finding-set blocks, and each set's added/observed/gone groups, with no
 *     overlap or horizontal overflow at 390px.
 *   - projects/hub/src/app/gardening/gardening-routines-page.shell-sweep.spec.ts —
 *     the gardening routines container's own list-beside-panel grid
 *     (blizzard#397): the list and panel sit side by side at 1280px, and
 *     genuinely collapse into a single stacked column at 390/320px, with no
 *     horizontal overflow of the layout itself.
 *   - projects/fleet/src/lib/kit/kit-dialog.shell-sweep.spec.ts — the modal
 *     shell (blizzard#399 D6): the scrim genuinely covers the full viewport,
 *     the panel centres itself and its own body scrolls a tall projection
 *     while the page behind it does not, and `CdkTrapFocus` keeps repeated
 *     real `Tab` presses cycling inside the panel rather than escaping to the
 *     page — real layout and focus-management claims jsdom cannot make.
 *   - projects/hub/src/app/gardening/gardening-run-dialog.shell-sweep.spec.ts —
 *     the gardening run dialog's own three fields (blizzard#399 D6), at the
 *     phone and desktop widths the dialog is reachable at: the scope field's
 *     radio rows genuinely stack, the delta baseline block's finding-set-id
 *     line genuinely sits above its per-repo landed-since lines, the
 *     new-scope near-match warning genuinely renders below both new-scope
 *     inputs, and the footer's Cancel/Run buttons genuinely sit side by side
 *     with neither overflowing the panel.
 *   - projects/hub/src/app/gardening/gardening-proposals-page.shell-sweep.spec.ts —
 *     the garden proposal docket container's own list-beside-panel grid: the
 *     list and panel sit side by side at 1280px, and genuinely collapse into
 *     a single stacked column at 390/320px, with no horizontal overflow of
 *     the layout itself.
 *   - projects/hub/src/app/gardening/gardening-proposal-pass-dialog.shell-sweep.spec.ts —
 *     the Pass dialog's footer: Cancel/Pass genuinely sit side by side with
 *     neither overflowing the panel, at phone and desktop widths.
 *   - projects/hub/src/app/gardening/gardening-proposal-accept-dialog.shell-sweep.spec.ts —
 *     the Accept dialog: the mint/decline radiogroup genuinely stacks its two
 *     options, the decline reason field genuinely renders below them once
 *     chosen, and the footer's Cancel/Accept sit side by side with neither
 *     overflowing the panel, at phone and desktop widths.
 *   - projects/fleet/src/lib/garden/gardening-findings-triage.shell-sweep.spec.ts —
 *     the findings triage list (`FleetFindingList`): with
 *     every row selected through the real select-all checkbox, the bulk bar's own
 *     buttons genuinely stay inside the viewport and never overlap each other or the
 *     list itself, at 1400/390/320px, and a `gone`-flagged row (D8) carries a
 *     genuinely different computed background/border-left color from a plain row.
 *   - projects/hub/src/app/gardening/gardening-finding-triage-dialog.shell-sweep.spec.ts —
 *     the findings triage dialog: the note field renders
 *     without overflowing the panel, the `supersede` verb's extra absorbing-finding
 *     field renders below/beside the note field with no overlap, and the footer's
 *     Cancel/submit buttons sit side by side without overflowing the panel, at
 *     1400/390/320px.
 */

const { spawnSync } = require('node:child_process');

const SWEEPS = [
  { project: 'hub', spec: 'projects/hub/src/app/nav/app-nav-menu.shell-sweep.spec.ts' },
  { project: 'hub', spec: 'projects/hub/src/app/board/chunk/chunk-page-layout.shell-sweep.spec.ts' },
  { project: 'hub', spec: 'projects/hub/src/app/board/chunk/chunk-artifacts-tab-layout.shell-sweep.spec.ts' },
  { project: 'runner', spec: 'projects/runner/src/app/nav/app-header.shell-sweep.spec.ts' },
  { project: 'local-panel', spec: 'projects/local-panel/src/lib/local-panel-mobile.shell-sweep.spec.ts' },
  { project: 'fleet', spec: 'projects/fleet/src/lib/runners/runner-view.shell-sweep.spec.ts' },
  { project: 'local-panel', spec: 'projects/local-panel/src/lib/transcript-panel.shell-sweep.spec.ts' },
  { project: 'local-panel', spec: 'projects/local-panel/src/lib/session-recovery-view.shell-sweep.spec.ts' },
  { project: 'runner', spec: 'projects/runner/src/app/nav/app-nav.shell-sweep.spec.ts' },
  { project: 'runner', spec: 'projects/runner/src/app/board/chunk/chunk-detail-page.shell-sweep.spec.ts' },
  { project: 'fleet', spec: 'projects/fleet/src/lib/design/hover-tint.shell-sweep.spec.ts' },
  { project: 'fleet', spec: 'projects/fleet/src/lib/chunk-detail/chunk-facts-alignment.shell-sweep.spec.ts' },
  { project: 'fleet', spec: 'projects/fleet/src/lib/board-card/board-card-control-row.shell-sweep.spec.ts' },
  { project: 'fleet', spec: 'projects/fleet/src/lib/graphs/graph-detail.shell-sweep.spec.ts' },
  { project: 'fleet', spec: 'projects/fleet/src/lib/garden/routine-panel.shell-sweep.spec.ts' },
  { project: 'fleet', spec: 'projects/fleet/src/lib/garden/garden-runs.shell-sweep.spec.ts' },
  { project: 'hub', spec: 'projects/hub/src/app/gardening/gardening-routines-page.shell-sweep.spec.ts' },
  { project: 'fleet', spec: 'projects/fleet/src/lib/kit/kit-dialog.shell-sweep.spec.ts' },
  { project: 'hub', spec: 'projects/hub/src/app/gardening/gardening-run-dialog.shell-sweep.spec.ts' },
  { project: 'hub', spec: 'projects/hub/src/app/gardening/gardening-proposals-page.shell-sweep.spec.ts' },
  { project: 'hub', spec: 'projects/hub/src/app/gardening/gardening-proposal-pass-dialog.shell-sweep.spec.ts' },
  { project: 'hub', spec: 'projects/hub/src/app/gardening/gardening-proposal-accept-dialog.shell-sweep.spec.ts' },
  { project: 'fleet', spec: 'projects/fleet/src/lib/garden/gardening-findings-triage.shell-sweep.spec.ts' },
  { project: 'hub', spec: 'projects/hub/src/app/gardening/gardening-finding-triage-dialog.shell-sweep.spec.ts' },
];

function runSweep({ project, spec }) {
  console.log(`\nshell-sweep: ${project} (${spec})\n`);
  const result = spawnSync(
    'npx',
    ['ng', 'test', project, '--browsers=ChromiumHeadless', '--watch=false', `--include=${spec}`],
    { stdio: 'inherit', shell: process.platform === 'win32' },
  );
  return result.status === 0;
}

function main() {
  const results = SWEEPS.map((sweep) => ({ ...sweep, ok: runSweep(sweep) }));
  const failed = results.filter((r) => !r.ok);

  if (failed.length > 0) {
    console.error(`\nshell-sweep: FAILED — ${failed.map((f) => f.project).join(', ')}\n`);
    process.exitCode = 1;
    return;
  }

  console.log('\nshell-sweep: all specs clean.\n');
}

main();
