import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { commands, page } from 'vitest/browser';

import type { RunnerRow } from './runner-rows';
import { RunnerPanelView } from './runner-view';

/** The design tokens are a global stylesheet loaded via each app's build `styles`,
 * never by a standalone component test (`board/hover-tint.shell-sweep.spec.ts`'s own
 * precedent) — read the sheet's real text server-side and inject it as a `<style>`
 * element, so the aging/stale `var(--amber)`/`var(--red)` colours this file's own
 * tier-colour assertions actually resolve. */
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
 * The board rail's compact runner panel, the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method — a real,
 * headless-Chromium proof that each runner's three lines (light/name/seen, agents, pause
 * badges) genuinely stack inside the rail at its ~390px width and at a desktop width with no
 * horizontal overflow, and that the pause badges are the kit's soft pills. jsdom never lays
 * these lines out, so this is a claim `web:unit-test` cannot make good on
 * (`bzh:narrow-viewport-tier-rule`).
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) because it
 * needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const NOW = new Date().toISOString();
const NAME = 'runner-with-a-genuinely-long-operator-chosen-name';

const ROW: RunnerRow = {
  runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3',
  runner_name: NAME,
  added_at: NOW,
  connection: 'online',
  workspace_id: 'ws_a',
  registered_at: NOW,
  last_seen_at: NOW,
  nowMs: Date.parse(NOW),
  served: [],
  unassigned: false,
  unresolved: [],
  bindingLines: [
    { project: 'Blizzard', workspaceId: 'ws_a', agents: 6, agentsInUse: 4, unreported: false },
    { project: 'Celestial Navigation Platform', workspaceId: 'ws_b', agents: 3, agentsInUse: 0, unreported: false },
  ],
  subscriptionName: null,
  online: true,
  hub_paused: true,
  locally_paused: true,
  locally_paused_reason: 'spend ceiling $5.00 reached over the trailing 24h (spend $7.00)',
  host_id: 'hs_1',
  claims: [],
  used: 0,
  subscriptionPaces: [],
};

const ROWS: readonly RunnerRow[] = [
  ROW,
  { ...ROW, runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYN7Q2M', hub_paused: false, locally_paused: false },
  {
    ...ROW,
    runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNNEW1',
    connection: 'never_connected',
    online: false,
    workspace_id: null,
    registered_at: null,
    last_seen_at: null,
    bindingLines: [],
    hub_paused: false,
    locally_paused: false,
  },
];

async function render(rows: readonly RunnerRow[] = ROWS) {
  await TestBed.configureTestingModule({
    imports: [RunnerPanelView],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(RunnerPanelView);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('rows', rows);
  await fixture.whenStable();
  return fixture;
}

describe('compact runner rail layout shell sweep (web:shell-sweep)', () => {
  it.each([390, 1280])('stacks each row\'s lines inside the rail with no horizontal overflow at %ipx', async (width) => {
    await loadDesignTokens();
    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const panel = root.querySelector<HTMLElement>('[data-testid="runner-panel"]')!;
      expect(panel.scrollWidth, `panel overflows horizontally at ${width}px`).toBeLessThanOrEqual(panel.clientWidth);

      const rows = Array.from(root.querySelectorAll<HTMLElement>('[data-testid="runner"]'));
      expect(rows).toHaveLength(3);
      for (const row of rows) {
        expect(row.scrollWidth, `runner row overflows horizontally at ${width}px`).toBeLessThanOrEqual(row.clientWidth);
      }

      const first = rows[0];
      const l1 = first.querySelector<HTMLElement>('.r1')!.getBoundingClientRect();
      const l2 = first.querySelector<HTMLElement>('[data-testid="runner-agents"]')!.getBoundingClientRect();
      const l3 = first.querySelector<HTMLElement>('.r3')!.getBoundingClientRect();
      expect(l2.top, 'agents line sits below the name line').toBeGreaterThanOrEqual(l1.bottom);
      expect(l3.top, 'badge line sits below the agents line').toBeGreaterThanOrEqual(l2.bottom);
      expect(l3.right).toBeLessThanOrEqual(panel.getBoundingClientRect().right + 1);

      // The unpaused runner draws no third line, and a runner with no bindings no second.
      expect(rows[1].querySelector('.r3')).toBeNull();
      expect(rows[2].querySelector('[data-testid="runner-agents"]')).toBeNull();
    } finally {
      root.remove();
    }
  });

  it.each([390, 1280])('draws both pause badges as soft kit pills in their tone colours at %ipx', async (width) => {
    await loadDesignTokens();
    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const row = root.querySelector<HTMLElement>(`[data-runner="${ROW.runner_id}"]`)!;
      const local = row.querySelector<HTMLElement>('[data-testid="runner-locally-paused"] .badge')!;
      const hub = row.querySelector<HTMLElement>('[data-testid="runner-hub-paused"] .badge')!;
      expect(local.textContent?.trim()).toBe('LOCALLY PAUSED');
      expect(hub.textContent?.trim()).toBe('HUB PAUSED');
      expect(getComputedStyle(local).color).toBe(tokenColor('--label-dim'));
      expect(getComputedStyle(hub).color).toBe(tokenColor('--amber-hi'));
      for (const badge of [getComputedStyle(local), getComputedStyle(hub)]) {
        expect(badge.borderTopStyle).toBe('solid');
        expect(parseFloat(badge.borderTopLeftRadius)).toBeGreaterThan(0);
        expect(parseFloat(badge.fontSize)).toBeLessThan(parseFloat(getComputedStyle(row).fontSize));
      }
    } finally {
      root.remove();
    }
  });
});
