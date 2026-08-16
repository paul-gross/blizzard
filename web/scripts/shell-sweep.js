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
 * The nine specs:
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
 */

const { spawnSync } = require('node:child_process');

const SWEEPS = [
  { project: 'hub', spec: 'projects/hub/src/app/nav/app-nav-menu.shell-sweep.spec.ts' },
  { project: 'hub', spec: 'projects/hub/src/app/board/chunk/chunk-page-layout.shell-sweep.spec.ts' },
  { project: 'runner', spec: 'projects/runner/src/app/nav/app-header.shell-sweep.spec.ts' },
  { project: 'local-panel', spec: 'projects/local-panel/src/lib/local-panel-mobile.shell-sweep.spec.ts' },
  { project: 'fleet', spec: 'projects/fleet/src/lib/runners/runner-view.shell-sweep.spec.ts' },
  { project: 'local-panel', spec: 'projects/local-panel/src/lib/transcript-panel.shell-sweep.spec.ts' },
  { project: 'local-panel', spec: 'projects/local-panel/src/lib/session-recovery-view.shell-sweep.spec.ts' },
  { project: 'runner', spec: 'projects/runner/src/app/nav/app-nav.shell-sweep.spec.ts' },
  { project: 'runner', spec: 'projects/runner/src/app/board/chunk/chunk-detail-page.shell-sweep.spec.ts' },
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

  console.log('\nshell-sweep: all nine specs clean.\n');
}

main();
