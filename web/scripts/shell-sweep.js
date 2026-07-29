// @ts-check
/*
 * The shell sweep (issue #171) — the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method.
 *
 * A real, headless-Chromium proof that the two shared shells — the hub board
 * and the runner's local panel — never let their header's profile menu drift
 * off-viewport as the window narrows, at every combination of shell, username
 * length, and viewport width the two specs below sweep. jsdom (this repo's
 * default unit-test environment) parses `@container` rules but never
 * evaluates them, so no jsdom spec can prove this; these two specs run
 * instead under `@angular/build:unit-test`'s real-browser mode
 * (`--browsers=ChromiumHeadless`, backed by `@vitest/browser-playwright`),
 * where layout, `@container` collapse, and hit-testing are all genuine.
 *
 * Named `*.shell-sweep.spec.ts` and excluded from each project's default
 * `ng test` run (`angular.json`'s per-project `test.exclude`) because they
 * need that real browser rather than jsdom — this script is the one
 * documented way to run them:
 *
 *   npm run shell-sweep   (from web/)
 *
 * The two specs:
 *   - projects/hub/src/app/nav/app-nav-menu.shell-sweep.spec.ts — the hub
 *     board shell (BoardHeader + AppNavMenu), swept over width only (no
 *     username is ever shown there).
 *   - projects/local-panel/src/lib/local-panel-layout.shell-sweep.spec.ts —
 *     the runner's local-panel shell (LocalPanelLayout), swept over width ×
 *     signed-in username length — the axis issue #163's actual defect lived
 *     on.
 */

const { spawnSync } = require('node:child_process');

const SWEEPS = [
  { project: 'hub', spec: 'projects/hub/src/app/nav/app-nav-menu.shell-sweep.spec.ts' },
  { project: 'local-panel', spec: 'projects/local-panel/src/lib/local-panel-layout.shell-sweep.spec.ts' },
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

  console.log('\nshell-sweep: both shells clean at every width/identity-length combination.\n');
}

main();
