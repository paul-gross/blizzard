import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { BoardHeader, hubApi } from 'fleet';
import { page } from 'vitest/browser';

import { AppNavMenu } from './app-nav-menu';

/**
 * The hub board shell's half of `web:shell-sweep`
 * (`blizzard-context:/verification/blizzard.md` bzh:web-shell-sweep) — a real,
 * headless-Chromium proof that `BoardHeader`'s narrowing collapse
 * (issue #163) never carries the profile menu off-viewport, at every width
 * from a wide monitor down to a phone forced into desktop mode.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`)
 * because it needs `--browsers=ChromiumHeadless`, not jsdom — run it via
 * `npm run shell-sweep` (`web/scripts/shell-sweep.js`), which drives both this
 * file and the runner shell's counterpart
 * (`local-panel/src/lib/shell-sweep.shell-sweep.spec.ts`).
 *
 * The hub shell projects only {@link AppNavMenu} into `BoardHeader`'s
 * `[header-trailing]` slot — no username, no other content-dependent width —
 * so, unlike the runner shell, sweeping identity length here would be a
 * needless no-op axis; width is the only variable that can move this shell's
 * menu, so width is the only one swept.
 */
@Component({
  selector: 'app-test-hub-shell',
  imports: [BoardHeader, AppNavMenu],
  template: `
    <fleet-board-header [chunks]="chunks" [spendToday]="spendToday" [spendYesterday]="spendYesterday">
      <app-nav-menu header-trailing />
    </fleet-board-header>
  `,
})
class TestHubShell {
  // A busy fleet across every lane plus both spend cells — the header's own
  // widest natural content, so a narrowing sweep tests the tightest fit it
  // ever actually renders rather than an emptier, easier-to-fit board.
  readonly chunks: readonly hubApi.ChunkSummary[] = (
    ['ready', 'running', 'waiting_on_human', 'needs_human', 'done', 'not_ready'] as const
  ).map((status, i) => ({
    chunk_id: `ch_${i}`,
    graph_id: 'gr_1',
    status,
    current_node_id: 'nd_build',
    work_refs: [],
  }));
  readonly spendToday: hubApi.FleetSpendView = {
    cost_usd: 12.34,
    cost_partial: false,
    input_tokens: 0,
    output_tokens: 0,
    cache_create_tokens: 0,
    cache_read_tokens: 0,
    since: '2026-07-29T00:00:00.000Z',
  };
  readonly spendYesterday: hubApi.FleetSpendView = { ...this.spendToday, cost_usd: 56.78 };
}

// 1400 down to 320 — spans a wide monitor to the narrowest common phone,
// straddling every breakpoint BoardHeader declares (1150px, 700px).
const WIDTHS = [1400, 1366, 1280, 1150, 1149, 1100, 1000, 900, 800, 768, 700, 699, 640, 600, 480, 390, 320];

describe('hub shell sweep (web:shell-sweep, issue #163/#171)', () => {
  it('keeps the profile menu on-screen, hit-testable, and overflow-free at every width', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    await TestBed.configureTestingModule({
      imports: [TestHubShell],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestHubShell);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      for (const width of WIDTHS) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const label = `width=${width}`;
        const menu = root.querySelector<HTMLElement>('[data-testid="app-nav-menu"]');
        expect(menu, `${label}: no profile menu trigger in the DOM`).not.toBeNull();
        const rect = menu!.getBoundingClientRect();

        expect(rect.width, `${label}: menu has zero width`).toBeGreaterThan(0);
        expect(rect.left, `${label}: menu's left edge is off-viewport (${rect.left})`).toBeGreaterThanOrEqual(0);
        expect(
          rect.right,
          `${label}: menu's right edge is past the viewport (${rect.right} > ${window.innerWidth})`,
        ).toBeLessThanOrEqual(window.innerWidth);

        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        expect(hit, `${label}: nothing hit-tests at the menu's own center`).not.toBeNull();
        expect(menu!.contains(hit), `${label}: the menu's center hit-tests to something outside it`).toBe(true);

        // Scoped to the header itself (this fixture mounts nothing else, but
        // matching the runner shell's own sweep keeps the two semantically
        // identical — see its comment for why it isn't document-wide).
        const header = root.querySelector<HTMLElement>('[data-testid="board-header"]')!;
        expect(
          header.scrollWidth,
          `${label}: the header overflows horizontally (${header.scrollWidth} > ${window.innerWidth})`,
        ).toBeLessThanOrEqual(window.innerWidth);
      }
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
