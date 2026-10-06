import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';
import { page } from 'vitest/browser';

import { LocalInfoView } from './app-info-view';

/**
 * The runner panel's info section with a runner imposing several gates — the tooled half
 * of `web:shell-sweep`: a real headless-Chromium proof that the Gates fact row stays
 * inside the panel at ~390px rather than forcing horizontal scroll
 * (`bzh:narrow-viewport-tier-rule`), and that the runner's full id stays whole on one line in the
 * desktop hub card's value column and at ~390px. Excluded from the default `ng test` run; run it via
 * `npm run shell-sweep`.
 */
const STATUS: runnerApi.RunnerStatusView = {
  runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3',
  runner_name: 'runner-local',
  workspace_id: 'workspace-local',
  pause: { local: false, hub: false, effective: false },
  capacities: { max_agents: 4, used: 1, free: 3 },
  hub: { endpoint: 'http://127.0.0.1:8421', reachable: true, last_contact_at: '2026-07-16T11:59:30.000Z', buffer_depth: 2 },
  last_tick_at: '2026-07-16T11:59:45.000Z',
  gates: ['build', 'review', 'deliver-to-master', 'a-node-with-a-rather-long-name-to-force-wrapping'],
};

/** The desktop hub card's content box — the layout's 330px right column less the card's 1px borders
 * and 8px side padding, which leaves the identity value a 216px column beside its 88px label. */
const DESKTOP_HUB_CARD_WIDTH = 312;

/** Where the registered identity row is swept: the desktop card's own width, and a phone's full width. */
const IDENTITY_LAYOUTS = [
  { viewport: 1280, hostWidth: DESKTOP_HUB_CARD_WIDTH },
  { viewport: 390, hostWidth: null },
] as const;

describe('runner info view gates row shell sweep (web:shell-sweep)', () => {
  it('shows the gates row inside the panel with no page errors or horizontal overflow at ~390px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    await TestBed.configureTestingModule({
      imports: [LocalInfoView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(LocalInfoView);
    fixture.componentRef.setInput('view', STATUS);
    fixture.componentRef.setInput('lastFlushLabel', '-30s');
    fixture.componentRef.setInput('lastTickLabel', '-15s');
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const row = root.querySelector<HTMLElement>('[data-testid="hub-gates"]')!;
      expect(row.textContent).toContain('deliver-to-master');
      expect(row.getBoundingClientRect().right).toBeLessThanOrEqual(document.documentElement.clientWidth);
      expect(
        root.scrollWidth,
        `info view overflows horizontally at 390px (${root.scrollWidth} > ${root.clientWidth})`,
      ).toBeLessThanOrEqual(root.clientWidth);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });

  it('shows a not-registered identity row inside the panel with no horizontal overflow at ~390px', async () => {
    await TestBed.configureTestingModule({
      imports: [LocalInfoView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(LocalInfoView);
    fixture.componentRef.setInput('view', { ...STATUS, runner_id: null, runner_name: 'runner-with-a-genuinely-long-operator-chosen-name' });
    fixture.componentRef.setInput('lastFlushLabel', '-30s');
    fixture.componentRef.setInput('lastTickLabel', '-15s');
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const row = root.querySelector<HTMLElement>('[data-testid="runner-identity"]')!;
      expect(row.textContent).toContain('not registered');
      expect(row.getBoundingClientRect().right).toBeLessThanOrEqual(document.documentElement.clientWidth);
      expect(
        root.scrollWidth,
        `info view overflows horizontally at 390px (${root.scrollWidth} > ${root.clientWidth})`,
      ).toBeLessThanOrEqual(root.clientWidth);
    } finally {
      root.remove();
    }
  });

  for (const { viewport, hostWidth } of IDENTITY_LAYOUTS) {
    it(`keeps the registered runner's full id whole on one line at ${viewport}px`, async () => {
      await TestBed.configureTestingModule({
        imports: [LocalInfoView],
        providers: [provideZonelessChangeDetection()],
      }).compileComponents();
      const fixture = TestBed.createComponent(LocalInfoView);
      fixture.componentRef.setInput('view', STATUS);
      fixture.componentRef.setInput('lastFlushLabel', '-30s');
      fixture.componentRef.setInput('lastTickLabel', '-15s');
      const root = fixture.nativeElement as HTMLElement;
      const host = document.createElement('div');
      if (hostWidth !== null) host.style.width = `${hostWidth}px`;
      host.appendChild(root);
      document.body.appendChild(host);
      await fixture.whenStable();

      try {
        await page.viewport(viewport, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const id = root.querySelector<HTMLElement>('[data-testid="runner-identity"] .rid')!;
        expect(id.textContent).toBe(STATUS.runner_id);
        expect(id.scrollWidth, `${viewport}px: the id is clipped (${id.scrollWidth} > ${id.clientWidth})`).toBeLessThanOrEqual(
          id.clientWidth,
        );
        const range = document.createRange();
        range.selectNodeContents(id);
        const lineTops = new Set(Array.from(range.getClientRects(), (r) => Math.round(r.top)));
        expect(lineTops.size, `${viewport}px: the id splits across ${lineTops.size} lines`).toBe(1);
        expect(
          root.scrollWidth,
          `info view overflows horizontally at ${viewport}px (${root.scrollWidth} > ${root.clientWidth})`,
        ).toBeLessThanOrEqual(root.clientWidth);
      } finally {
        host.remove();
      }
    });
  }
});
