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
 * The three specs:
 *   - projects/hub/src/app/nav/app-nav-menu.shell-sweep.spec.ts — the hub
 *     board shell (BoardHeader + AppNavMenu), swept over width only (no
 *     username is ever shown there): never lets the profile menu drift
 *     off-viewport as the window narrows.
 *   - projects/local-panel/src/lib/local-panel-layout.shell-sweep.spec.ts —
 *     the runner's local-panel shell (LocalPanelLayout), swept over width ×
 *     signed-in username length — the axis issue #163's actual defect lived
 *     on: same profile-menu-drift proof.
 *   - projects/local-panel/src/lib/local-panel-mobile.shell-sweep.spec.ts —
 *     the runner's mobile chunk list (`LocalPanelMobile` → `ChunkCard`,
 *     issue #176): a five-work-item chunk card's per-line
 *     `-webkit-line-clamp: 2` lines genuinely stack (distinct
 *     `getBoundingClientRect().top`s) with no horizontal overflow, at the
 *     narrow phone widths this component — unlike `ChunkRow` — is actually
 *     reached at, beneath the persistent mobile bottom tab bar.
 */

const { spawnSync } = require('node:child_process');

const SWEEPS = [
  { project: 'hub', spec: 'projects/hub/src/app/nav/app-nav-menu.shell-sweep.spec.ts' },
  { project: 'local-panel', spec: 'projects/local-panel/src/lib/local-panel-layout.shell-sweep.spec.ts' },
  { project: 'local-panel', spec: 'projects/local-panel/src/lib/local-panel-mobile.shell-sweep.spec.ts' },
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

  console.log('\nshell-sweep: all three specs clean.\n');
}

main();
