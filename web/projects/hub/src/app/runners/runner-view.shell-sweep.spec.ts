import { RunnerConnection, type RunnerRegistryView } from 'fleet';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { commands, page } from 'vitest/browser';

import type { RunnerRow } from './runner-rows.model';
import { RunnerPanelView } from './runner-view';

const REGISTRY: RunnerRegistryView = {
  agents: null,
  runner_id: '',
  runner_name: '',
  added_at: '',
  added_by: null,
  bindings: null,
  capabilities: [],
  connection: RunnerConnection.NEVER_CONNECTED,
  env_capacity: null,
  gates: [],
  host_id: null,
  hub_paused: false,
  last_seen_at: null,
  locally_paused: false,
  locally_paused_by: null,
  locally_paused_reason: null,
  online: false,
  projects: [],
  registered_at: null,
  retired: false,
  retired_at: null,
  retired_by: null,
  subscription: null,
  subscription_unrostered: false,
  subscriptions: [],
  version: null,
  version_refusal: null,
  version_standing: null,
  workspace_id: null,
};


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
 * headless-Chromium proof that each runner's lines (light/name/pause marks/seen, then one agents
 * bar per binding, its name and cells on one line) genuinely stack inside the rail at its ~390px
 * width and at a desktop width with no horizontal overflow, and that the brake glyphs wear their
 * colours. jsdom never lays
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
  ...REGISTRY,
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
      const bars = first.querySelectorAll<HTMLElement>('[data-testid="runner-agents"]');
      expect(bars, 'one agents bar per binding').toHaveLength(2);
      const l2 = bars[bars.length - 1].getBoundingClientRect();
      expect(l2.top, 'agents line sits below the name line').toBeGreaterThanOrEqual(l1.bottom);
      for (const mark of first.querySelectorAll<HTMLElement>('fleet-kit-glyph')) {
        const box = mark.getBoundingClientRect();
        expect(box.top, 'pause marks sit on the name line').toBeGreaterThanOrEqual(l1.top - 1);
        expect(box.bottom, 'pause marks sit on the name line').toBeLessThanOrEqual(l1.bottom + 1);
      }
      for (const bar of bars) {
        const name = bar.querySelector<HTMLElement>('[data-testid="slot-bar-name"]')!.getBoundingClientRect();
        const gauge = bar.querySelector<HTMLElement>('[data-testid="slot-bar-gauge"]')!.getBoundingClientRect();
        expect(gauge.left, 'the cells sit right of the name, on one line').toBeGreaterThanOrEqual(name.right);
        expect(gauge.right).toBeLessThanOrEqual(panel.getBoundingClientRect().right + 1);
      }

      // A runner with no bindings draws no agents line.
      expect(rows[2].querySelector('[data-testid="runner-agents"]')).toBeNull();
    } finally {
      root.remove();
    }
  });

  it.each([390, 1280])('draws the pause in the paused yellow and the stop in red, each light in a fixed-width slot, at %ipx', async (width) => {
    await loadDesignTokens();
    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const row = root.querySelector<HTMLElement>(`[data-runner="${ROW.runner_id}"]`)!;
      const local = row.querySelector<SVGElement>('[data-testid="runner-locally-paused"] svg')!;
      const hub = row.querySelector<SVGElement>('[data-testid="runner-hub-paused"] svg')!;
      expect(getComputedStyle(local).stroke).toBe(tokenColor('--amber'));
      expect(getComputedStyle(hub).stroke).toBe(tokenColor('--red'));
      const lights = [...root.querySelectorAll<HTMLElement>('[data-testid="runner-light"]')].map((l) => l.getBoundingClientRect());
      expect(new Set(lights.map((l) => l.width)).size, 'every light slot is one width').toBe(1);
      expect(new Set(lights.map((l) => l.left)).size, 'every light starts at one column').toBe(1);
      for (const mark of [local, hub]) {
        expect(mark.getBoundingClientRect().width).toBeGreaterThan(0);
      }
    } finally {
      root.remove();
    }
  });
});
