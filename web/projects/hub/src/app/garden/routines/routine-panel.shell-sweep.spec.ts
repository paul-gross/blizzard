import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

import { FleetRoutinePanel, type RoutinePanelVm } from './routine-panel';

/** The design tokens are a global stylesheet the app build loads, never a standalone mount —
 * injected so the kit badges' tone colours resolve to real colours. */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

/** The computed colour a design token resolves to, read off a probe element. */
function tokenColor(token: string): string {
  const probe = document.createElement('span');
  probe.style.color = `var(${token})`;
  document.body.appendChild(probe);
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color;
}

/**
 * The gardening routine panel's health blocks (the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method) — a real,
 * headless-Chromium proof that the record, related-scopes, strategy, trend,
 * measurement, and last-swept blocks genuinely stack at phone widths with no
 * horizontal overflow, and that the last-swept table's own long revision hashes wrap
 * inside their column rather than forcing the table wider than its section. jsdom lays
 * out a flex column and a
 * `table-layout: fixed` grid without ever checking whether either actually clips, so
 * this is exactly the class of layout claim `web:unit-test` cannot make good on
 * (`bzh:narrow-viewport-tier-rule`) — gardening sits in the hub's mobile bottom tab
 * bar, so the narrow widths are load-bearing, not incidental.
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`)
 * because it needs `--browsers=ChromiumHeadless`, not jsdom — run it via
 * `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const VM: RoutinePanelVm = {
  record: {
    name: 'nightly',
    graphName: 'garden-routine',
    defaultScopeSlug: 'blizzard',
    defaultModel: ['claude-sonnet-5'],
    defaultEffort: 'medium',
  },
  blockedReason: null,
  strategy: [
    { name: 'survey', prompt: 'Survey the repo for stale docstrings and dead code, one weed per finding.' },
    { name: 'deliver', prompt: 'Record the delta against the routine baseline.' },
  ],
  trend: { created: 4, outflow: 2, withdrawn: 1, reopened: 1 },
  measurements: [
    { scopeSlug: 'blizzard', producedAt: '2026-01-10T00:00:00Z', measurement: '3 findings resolved this sweep' },
  ],
  lastSwept: [
    {
      scopeSlug: 'blizzard',
      findingSetId: 'fins_01ABCDEFGHJKMNPQRSTVWXYZ0123',
      producedAt: '2026-01-10T00:00:00Z',
      revisionsLabel: 'blizzard@0123456789abcdef0123456789abcdef01234567, blizzard-context@fedcba9876543210fedcba9876543210fedcba98',
    },
    { scopeSlug: 'never-swept-scope', findingSetId: null, producedAt: null, revisionsLabel: '—' },
  ],
  windowLabel: 'last 28 days',
  relatedScopes: [{ slug: 'blizzard', isDefault: true }],
  retired: false,
  renderedRetired: false,
};

async function render(vm: RoutinePanelVm = VM, canEdit = false) {
  await TestBed.configureTestingModule({
    imports: [FleetRoutinePanel],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(FleetRoutinePanel);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('vm', vm);
  fixture.componentRef.setInput('canEdit', canEdit);
  await fixture.whenStable();
  return fixture;
}

describe('gardening routine panel layout shell sweep (web:shell-sweep, blizzard#397)', () => {
  it.each([1280, 390, 320])('stacks every block with no horizontal overflow at %ipx', async (width) => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 900);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const panel = root.querySelector<HTMLElement>('[data-testid="gardening-routine-panel"]')!;
      expect(panel).not.toBeNull();

      const blockIds = [
        'gardening-routine-record',
        'gardening-routine-scopes',
        'gardening-routine-strategy',
        'gardening-routine-trend',
        'gardening-routine-measurements',
        'gardening-routine-last-swept',
      ];
      const blocks = blockIds.map((id) => root.querySelector<HTMLElement>(`[data-testid="${id}"]`)!);
      for (const block of blocks) expect(block).not.toBeNull();

      const tops = blocks.map((b) => b.getBoundingClientRect().top);
      expect(new Set(tops).size, `blocks did not stack — tops were ${tops.join(', ')}`).toBe(blocks.length);

      expect(
        panel.scrollWidth,
        `panel overflows horizontally at ${width}px (${panel.scrollWidth} > ${panel.clientWidth})`,
      ).toBeLessThanOrEqual(panel.clientWidth);

      const table = root.querySelector<HTMLElement>('[data-testid="gardening-routine-last-swept"] table')!;
      expect(
        table.scrollWidth,
        `last-swept table overflows its own width at ${width}px (${table.scrollWidth} > ${table.clientWidth})`,
      ).toBeLessThanOrEqual(table.clientWidth);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});

describe('gardening routine record label column shell sweep (web:shell-sweep)', () => {
  it.each([1280, 390, 320])('keeps every record label on one line at %ipx', async (width) => {
    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 900);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      // `default scope`/`default model`/`default effort` are all wider than the kit
      // fact grid's own 74px label column and wrapped to a second line against it —
      // `routine-panel.css` sizes this list's column to `max-content` instead. Line
      // count is what the fix is about, so the claim is that every label is exactly
      // as tall as the shortest one (`graph`, one line at any width): a wrapped
      // label is taller, and jsdom lays out text without ever measuring it.
      const labels = Array.from(
        root.querySelectorAll<HTMLElement>('[data-testid="gardening-routine-record-facts"] dt'),
      );
      expect(labels.map((l) => l.textContent?.trim())).toEqual([
        'graph',
        'default scope',
        'default model',
        'default effort',
      ]);
      const heights = labels.map((l) => l.getBoundingClientRect().height);
      expect(
        new Set(heights.map((h) => Math.round(h))).size,
        `a record label wrapped at ${width}px — heights were ${heights.join(', ')}`,
      ).toBe(1);
    } finally {
      root.remove();
    }
  });
});

describe('gardening routine panel lifecycle controls shell sweep (web:shell-sweep)', () => {
  it.each([1280, 390, 320])(
    'renders the retired state badge and the Re-enable control with no overflow at %ipx',
    async (width) => {
      await loadDesignTokens();
      const fixture = await render({ ...VM, retired: true, renderedRetired: true }, true);
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await fixture.whenStable();

      try {
        await page.viewport(width, 900);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const record = root.querySelector<HTMLElement>('[data-testid="gardening-routine-record"]')!;
        expect(
          record.scrollWidth,
          `record header overflows at ${width}px (${record.scrollWidth} > ${record.clientWidth})`,
        ).toBeLessThanOrEqual(record.clientWidth);

        const state = root.querySelector('[data-testid="gardening-routine-panel-state"]');
        expect(state?.textContent?.trim()).toBe('retired');
        const badge = state!.querySelector<HTMLElement>('.badge')!;
        expect(getComputedStyle(badge).color, `${width}px: retired state is not the lifecycle red`).toBe(tokenColor('--red'));
        expect(getComputedStyle(badge).textTransform).toBe('uppercase');
        expect(root.querySelector('[data-testid="gardening-routine-panel-enable"]')).not.toBeNull();
        expect(root.querySelector('[data-testid="gardening-routine-run"]')).toBeNull();
        expect(root.querySelector('[data-testid="gardening-routine-retired-notice"]')).not.toBeNull();
      } finally {
        root.remove();
      }
    },
  );

  it.each([1280, 390])('renders the enabled state as a cyan kit label at %ipx', async (width) => {
    await loadDesignTokens();
    const fixture = await render(VM, true);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 900);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const state = root.querySelector<HTMLElement>('[data-testid="gardening-routine-panel-state"]')!;
      expect(state.textContent?.trim()).toBe('enabled');
      expect(getComputedStyle(state.querySelector<HTMLElement>('.badge')!).color).toBe(tokenColor('--cyan'));
    } finally {
      root.remove();
    }
  });
});
