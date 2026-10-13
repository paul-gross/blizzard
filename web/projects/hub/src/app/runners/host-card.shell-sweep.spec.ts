import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

import type { HostCard } from './host-rows.model';
import type { RunnerRow } from './runner-rows';
import { RunnerPanelView } from './runner-view';

/**
 * The desktop rail's host card (the tooled half of `blizzard-context:/verification/blizzard.md`'s
 * `web:shell-sweep` method) — a real, headless-Chromium proof that a host card holding two runners
 * stays inside the runner panel with its rows stacked, and that the host-less heading and its row
 * sit below it. The mobile Fleet screen's half of the same claim is in
 * `runners-view.shell-sweep.spec.ts`; jsdom never lays out the wrapping binding lines, so this is
 * a claim `web:unit-test` cannot make good on (`bzh:narrow-viewport-tier-rule`).
 *
 * Excluded from the default `ng test hub` run — run it via `npm run shell-sweep`.
 */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

const NOW = new Date().toISOString();

const ROW: RunnerRow = {
  runner_id: 'rn_base',
  runner_name: 'base',
  added_at: NOW,
  connection: 'online',
  workspace_id: 'ws_a',
  registered_at: NOW,
  last_seen_at: NOW,
  nowMs: Date.parse(NOW),
  served: ['Default'],
  unassigned: false,
  unresolved: [],
  bindingLines: [],
  subscriptionName: null,
  online: true,
  hub_paused: false,
  locally_paused: false,
  env_capacity: 4,
  used: 1,
  claims: [{ chunkId: 'ch_01claim000000000000000000000', shortId: 'C-01CLAIM0000000000000000', node: 'build', status: 'running' }],
  subscriptionPaces: [],
};

describe('runner registry host card layout shell sweep (web:shell-sweep)', () => {
  const LONG = 'workspace-with-a-genuinely-long-operator-chosen-identifier';
  const CARD: HostCard = {
    hostId: 'hs_01KXKVVF1J3D6H6VYZ3XYNHOST',
    hostName: 'build-host-with-a-genuinely-long-operator-chosen-name',
    online: true,
    connection: 'online',
    reportLabel: 'seen 4s ago',
    workspaces: [
      { workspaceId: LONG, capacity: 3, inUse: 2, flag: null, environments: [] },
      { workspaceId: 'ws-2', capacity: null, inUse: 0, flag: null, environments: [] },
    ],
    subscriptionPaces: [
      {
        slug: 'anthropic',
        name: 'Anthropic (default) — a genuinely long subscription display name',
        paceBars: [{ window: '5h', utilizationPct: 40, elapsedPct: 20 }],
        condition: null,
        sampledAt: NOW,
        refreshedLabel: 'refreshed 0s ago',
        freshness: 'fresh',
        missReason: null,
        spenders: ['runner-alpha-with-a-long-name', 'runner-bravo-with-a-long-name'],
      },
    ],
  };
  const HOSTED: readonly RunnerRow[] = [
    {
      ...ROW,
      runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNALFA',
      runner_name: 'runner-alpha-with-a-long-name',
      host_id: CARD.hostId,
      subscriptionPaces: [],
      bindingLines: [
        { project: 'Celestial Navigation Platform', workspaceId: LONG, agents: 3, agentsInUse: 2, unreported: false },
        { project: 'Blizzard', workspaceId: 'ws-unreported-by-its-host', agents: 1, agentsInUse: 0, unreported: true },
      ],
      subscriptionName: 'Anthropic (default) — a genuinely long subscription display name',
    },
    {
      ...ROW,
      runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNBRAV',
      runner_name: 'runner-bravo-with-a-long-name',
      host_id: CARD.hostId,
      subscriptionPaces: [],
      bindingLines: [{ project: 'Blizzard', workspaceId: 'ws-2', agents: 1, agentsInUse: 1, unreported: false }],
      subscriptionName: 'zzz-a-subscription-slug-the-host-roster-never-declared',
      subscription_unrostered: true,
    },
    { ...ROW, runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNHLSS', runner_name: 'host-less', host_id: null, subscriptionPaces: [] },
  ];

  it.each([390, 1280])('holds a two-runner host card and a host-less row inside the panel with no horizontal overflow at %ipx', async (width) => {
    await loadDesignTokens();
    await TestBed.configureTestingModule({
      imports: [RunnerPanelView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', HOSTED);
    fixture.componentRef.setInput('cards', [CARD]);
    fixture.componentRef.setInput('canPause', true);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 900);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const panel = root.querySelector<HTMLElement>('[data-testid="runner-panel"]')!;
      const panelRect = panel.getBoundingClientRect();
      expect(panel.scrollWidth, `panel overflows horizontally at ${width}px`).toBeLessThanOrEqual(panel.clientWidth);

      const card = root.querySelector<HTMLElement>('[data-testid="host-card"]')!;
      expect(card.scrollWidth, `host card overflows horizontally at ${width}px`).toBeLessThanOrEqual(card.clientWidth);
      expect(card.getBoundingClientRect().right).toBeLessThanOrEqual(panelRect.right + 1);

      const hostedRows = Array.from(card.querySelectorAll<HTMLElement>('[data-testid="runner"]'));
      expect(hostedRows).toHaveLength(2);
      expect(hostedRows[1].getBoundingClientRect().top).toBeGreaterThanOrEqual(hostedRows[0].getBoundingClientRect().bottom);
      for (const row of hostedRows) {
        expect(row.scrollWidth, `hosted runner row overflows horizontally at ${width}px`).toBeLessThanOrEqual(row.clientWidth);
      }

      // The long workspace id, binding lines, spender names and flagged subscription each wrap inside the card.
      const marks = card.querySelectorAll<HTMLElement>(
        '[data-testid="workspace-row"], [data-testid="runner-binding"], [data-testid="runner-binding-unreported"], [data-testid="runner-subscription-unrostered"], [data-testid="host-subscriptions"]',
      );
      expect(marks.length).toBeGreaterThanOrEqual(6);
      for (const mark of marks) {
        const rect = mark.getBoundingClientRect();
        expect(rect.width, `${mark.textContent} has no width`).toBeGreaterThan(0);
        expect(rect.right, `${mark.textContent} is past the card at ${width}px`).toBeLessThanOrEqual(card.getBoundingClientRect().right + 1);
      }

      // The host-less heading and its row sit below the card.
      const heading = root.querySelector<HTMLElement>('[data-testid="hostless-heading"]')!;
      expect(heading.getBoundingClientRect().top).toBeGreaterThanOrEqual(card.getBoundingClientRect().bottom);
      const hostless = root.querySelector<HTMLElement>('[data-testid="runner-list"] [data-testid="runner"]')!;
      expect(hostless.getAttribute('data-runner')).toBe('rn_01KXKVVF1J3D6H6VYZ3XYNHLSS');
      expect(hostless.getBoundingClientRect().top).toBeGreaterThanOrEqual(heading.getBoundingClientRect().bottom);
      expect(hostless.scrollWidth).toBeLessThanOrEqual(hostless.clientWidth);
    } finally {
      root.remove();
    }
  });
});
