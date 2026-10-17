import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';
import type { HostCard } from './host-rows.model';
import type { RunnerRow } from './runner-rows';

import { RunnersView } from './runners-view';

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

/**
 * The mobile Fleet screen's runner cards (the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method) — a real,
 * headless-Chromium proof that each runner card genuinely stacks below the last with
 * no horizontal overflow at phone widths, and that a card carrying claims, a slot bar,
 * and grouped subscription pace bars stays inside its own width even with a long
 * chunk id and a long subscription name. jsdom lays out this flex column without ever
 * checking whether the pace bars or the claim line actually clip, so this is exactly
 * the class of layout claim `web:unit-test` cannot make good on
 * (`bzh:narrow-viewport-tier-rule`) — Fleet sits in the hub's mobile bottom tab bar,
 * so the narrow widths are load-bearing, not incidental.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`)
 * because it needs `--browsers=ChromiumHeadless`, not jsdom — run it via
 * `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const NOW = new Date().toISOString();

const ROWS: readonly RunnerRow[] = [
  {
    runner_id: 'rn_online',
    runner_name: 'online',
    added_at: NOW,
    connection: 'online',
    workspace_id: 'ws_a',
    registered_at: NOW,
    last_seen_at: NOW,
    nowMs: Date.parse(NOW),
    served: [],
    unassigned: false,
    unresolved: [],
    bindingLines: [],
    subscriptionName: null,
    online: true,
    hub_paused: false,
    locally_paused: false,
    env_capacity: 4,
    used: 2,
    claims: [
      { chunkId: 'ch_01claim000000000000000000000', shortId: 'C-01CLAIM0000000000000000', node: 'build', status: 'running' },
    ],
    subscriptionPaces: [
      {
        slug: 'anthropic-default',
        name: 'Anthropic (default) — a genuinely long subscription display name',
        condition: null,
        paceBars: [
          { window: '5h', utilizationPct: 40, elapsedPct: 20 },
          { window: '7d', utilizationPct: 70, elapsedPct: 55 },
        ],
        sampledAt: NOW,
        refreshedLabel: 'refreshed 0s ago',
        freshness: 'fresh',
        missReason: null,
      },
    ],
  },
  {
    runner_id: 'rn_paused',
    runner_name: 'paused',
    added_at: NOW,
    connection: 'offline',
    workspace_id: 'ws_a',
    registered_at: NOW,
    last_seen_at: NOW,
    nowMs: Date.parse(NOW),
    served: [],
    unassigned: false,
    unresolved: [],
    bindingLines: [],
    subscriptionName: null,
    online: false,
    hub_paused: true,
    locally_paused: true,
    locally_paused_reason: 'spend ceiling $5.00 reached over the trailing 24h (spend $7.00)',
    used: 0,
    claims: [],
    subscriptionPaces: [
      {
        slug: 'anthropic-default',
        name: 'Anthropic (default)',
        condition: null,
        paceBars: [],
        sampledAt: NOW,
        refreshedLabel: 'refreshed 0s ago',
        freshness: 'fresh',
        missReason: null,
      },
    ],
  },
  {
    runner_id: 'rn_retired',
    runner_name: 'retired',
    added_at: NOW,
    connection: 'offline',
    workspace_id: 'ws_a',
    registered_at: NOW,
    last_seen_at: NOW,
    nowMs: Date.parse(NOW),
    served: [],
    unassigned: false,
    unresolved: [],
    bindingLines: [],
    subscriptionName: null,
    online: false,
    hub_paused: false,
    locally_paused: false,
    retired: true,
    retired_at: NOW,
    retired_by: 'a-genuinely-long-operator-name@example.com',
    used: 0,
    claims: [],
    subscriptionPaces: [],
  },
  {
    runner_id: 'rn_freshness',
    runner_name: 'freshness',
    added_at: NOW,
    connection: 'online',
    workspace_id: 'ws_a',
    registered_at: NOW,
    last_seen_at: NOW,
    nowMs: Date.parse(NOW),
    served: [],
    unassigned: false,
    unresolved: [],
    bindingLines: [],
    subscriptionName: null,
    online: true,
    hub_paused: false,
    locally_paused: false,
    used: 0,
    claims: [],
    // The aging/stale tier colours and a long-miss-reason "no sample yet" row, the
    // shapes the mobile shell sweep must also prove.
    subscriptionPaces: [
      {
        slug: 'aging',
        name: 'Aging',
        condition: null,
        paceBars: [{ window: '5h', utilizationPct: 40, elapsedPct: 60 }],
        sampledAt: NOW,
        refreshedLabel: 'refreshed 30m ago',
        freshness: 'aging',
        missReason: null,
      },
      {
        slug: 'stale',
        name: 'Stale',
        condition: null,
        paceBars: [{ window: '5h', utilizationPct: 40, elapsedPct: 100 }],
        sampledAt: NOW,
        refreshedLabel: 'refreshed 2h ago',
        freshness: 'stale',
        missReason: null,
      },
      {
        slug: 'never',
        name: 'Never',
        condition: null,
        paceBars: [],
        sampledAt: null,
        refreshedLabel: null,
        freshness: null,
        missReason: 'endpoint_unreachable',
      },
    ],
  },
  // Added at the hub but never started: no workspace, contact, or capabilities yet.
  {
    runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNNEW1',
    runner_name: 'runner-with-a-genuinely-long-operator-chosen-name',
    added_at: NOW,
    connection: 'never_connected',
    workspace_id: null,
    registered_at: null,
    last_seen_at: null,
    nowMs: Date.parse(NOW),
    served: [],
    unassigned: false,
    unresolved: [],
    bindingLines: [],
    subscriptionName: null,
    online: false,
    hub_paused: false,
    locally_paused: false,
    used: 0,
    claims: [],
    subscriptionPaces: [],
  },
  // The same long name on a second runner: each card keeps its own full id.
  {
    runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYN7Q2M',
    runner_name: 'runner-with-a-genuinely-long-operator-chosen-name',
    added_at: NOW,
    connection: 'online',
    workspace_id: 'ws_a',
    registered_at: NOW,
    last_seen_at: NOW,
    nowMs: Date.parse(NOW),
    // Serving several long-named projects, with an entry naming no project of the tenant.
    served: ['Celestial Navigation Platform', 'Northwind Interplanetary Logistics', 'Blizzard'],
    unassigned: false,
    unresolved: ['permafrost-archive-and-recovery-service'],
    bindingLines: [],
    subscriptionName: null,
    online: true,
    hub_paused: false,
    locally_paused: false,
    used: 0,
    claims: [],
    subscriptionPaces: [],
  },
];

async function render() {
  await TestBed.configureTestingModule({
    imports: [RunnersView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(RunnersView);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('rows', ROWS);
  fixture.componentRef.setInput('canPause', true);
  fixture.componentRef.setInput('showRetired', true);
  await fixture.whenStable();
  return fixture;
}

describe('mobile Fleet screen layout shell sweep (web:shell-sweep)', () => {
  it.each([1280, 390, 320])('stacks every runner card with no horizontal overflow at %ipx', async (width) => {
    await loadDesignTokens();
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

      const panel = root.querySelector<HTMLElement>('[data-testid="mobile-fleet-panel"]')!;
      expect(panel).not.toBeNull();

      const cards = Array.from(root.querySelectorAll<HTMLElement>('[data-testid="mobile-fleet-runner"]'));
      expect(cards).toHaveLength(6);

      const rects = cards.map((c) => c.getBoundingClientRect());
      for (let i = 1; i < rects.length; i++) {
        expect(
          rects[i].top,
          `runner card ${i} overlaps the previous one — top ${rects[i].top} < previous bottom ${rects[i - 1].bottom}`,
        ).toBeGreaterThanOrEqual(rects[i - 1].bottom);
      }

      for (const card of cards) {
        expect(
          card.scrollWidth,
          `runner card overflows horizontally at ${width}px (${card.scrollWidth} > ${card.clientWidth})`,
        ).toBeLessThanOrEqual(card.clientWidth);
      }

      // The claim line's long chunk id and the subscription group's long name are the
      // two spans most likely to force a card wider than its own box.
      // The served projects and the unresolved entry wrap inside their card.
      const servedMarks = root.querySelectorAll<HTMLElement>(
        '[data-runner="rn_01KXKVVF1J3D6H6VYZ3XYN7Q2M"] [data-testid="runner-served-project"], [data-runner="rn_01KXKVVF1J3D6H6VYZ3XYN7Q2M"] [data-testid="runner-unresolved"]',
      );
      expect(servedMarks).toHaveLength(4);
      for (const mark of servedMarks) {
        const rect = mark.getBoundingClientRect();
        expect(rect.width, `${mark.textContent} has no width`).toBeGreaterThan(0);
        expect(rect.right, `${mark.textContent} is past the panel at ${width}px`).toBeLessThanOrEqual(
          panel.getBoundingClientRect().right + 1,
        );
      }

      const claim = root.querySelector<HTMLElement>('[data-runner="rn_online"] [data-testid="mobile-fleet-runner-claim"]')!;
      expect(claim.getBoundingClientRect().right).toBeLessThanOrEqual(cards[0].getBoundingClientRect().right + 1);

      // A retired row's marker carries the operator's name, which can be long: it must stay
      // inside its own card rather than widening it.
      const retiredCard = root.querySelector<HTMLElement>('[data-runner="rn_retired"]')!;
      const retiredBadge = retiredCard.querySelector<HTMLElement>('[data-testid="mobile-fleet-runner-retired"]')!;
      expect(retiredBadge).not.toBeNull();
      expect(retiredBadge.getBoundingClientRect().right).toBeLessThanOrEqual(retiredCard.getBoundingClientRect().right + 1);

      const subName = root.querySelector<HTMLElement>('[data-testid="subscription-pace-group-name"]')!;
      expect(subName.getBoundingClientRect().right).toBeLessThanOrEqual(cards[0].getBoundingClientRect().right + 1);

      const report = root.querySelector<HTMLElement>('[data-testid="subscription-pace-group-unsampled"]')!;
      expect(report.textContent?.trim()).toBe('NO USAGE WINDOWS REPORTED');
      expect(report.getAttribute('aria-label')).toBe('Anthropic (default) sample reported no usage windows');

      // The aging/stale tier colours are genuinely computed and distinguishable, and
      // a long miss reason on a never-sampled row still fits inside its card.
      const agingCard = root.querySelector<HTMLElement>('[data-runner="rn_freshness"]')!;
      const aging = agingCard.querySelector<HTMLElement>(
        '[data-subscription-slug="aging"] [data-testid="subscription-pace-group-refreshed"]',
      )!;
      const stale = agingCard.querySelector<HTMLElement>(
        '[data-subscription-slug="stale"] [data-testid="subscription-pace-group-refreshed"]',
      )!;
      const agingColor = getComputedStyle(aging).color;
      const staleColor = getComputedStyle(stale).color;
      expect(agingColor).not.toBe(staleColor);
      expect(agingColor).not.toBe(getComputedStyle(root).color);

      const never = agingCard.querySelector<HTMLElement>(
        '[data-subscription-slug="never"] [data-testid="subscription-pace-group-no-sample"]',
      )!;
      expect(never.textContent?.trim()).toBe('NO SAMPLE YET — endpoint_unreachable');
      expect(never.getBoundingClientRect().right).toBeLessThanOrEqual(agingCard.getBoundingClientRect().right + 1);

      expect(
        panel.scrollWidth,
        `panel overflows horizontally at ${width}px (${panel.scrollWidth} > ${panel.clientWidth})`,
      ).toBeLessThanOrEqual(panel.clientWidth);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});

describe('mobile Fleet screen host card layout shell sweep (web:shell-sweep)', () => {
  const LONG = 'workspace-with-a-genuinely-long-operator-chosen-identifier';
  const CARD: HostCard = {
    hostId: 'hs_01KXKVVF1J3D6H6VYZ3XYNHOST',
    hostName: 'build-host-with-a-genuinely-long-operator-chosen-name',
    online: true,
    connection: 'online',
    reportLabel: 'seen 4s ago',
    url: null,
    enrolled: true,
    registeredAt: null,
    addedAt: '2026-01-01T00:00:00Z',
    addedBy: null,
    lastSeenLabel: 'never',
    heldChunks: new Map(),
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
      ...ROWS[0],
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
      ...ROWS[0],
      runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNBRAV',
      runner_name: 'runner-bravo-with-a-long-name',
      host_id: CARD.hostId,
      subscriptionPaces: [],
      bindingLines: [{ project: 'Blizzard', workspaceId: 'ws-2', agents: 1, agentsInUse: 1, unreported: false }],
      subscriptionName: 'zzz-a-subscription-slug-the-host-roster-never-declared',
      subscription_unrostered: true,
    },
    { ...ROWS[0], runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNHLSS', runner_name: 'host-less', host_id: null, subscriptionPaces: [] },
  ];

  it.each([390, 320])('holds a two-runner host card and a host-less row inside the panel with no horizontal overflow at %ipx', async (width) => {
    await loadDesignTokens();
    await TestBed.configureTestingModule({
      imports: [RunnersView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(RunnersView);
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

      const panel = root.querySelector<HTMLElement>('[data-testid="mobile-fleet-panel"]')!;
      const panelRect = panel.getBoundingClientRect();
      expect(panel.scrollWidth, `panel overflows horizontally at ${width}px`).toBeLessThanOrEqual(panel.clientWidth);

      const card = root.querySelector<HTMLElement>('[data-testid="host-card"]')!;
      expect(card.scrollWidth, `host card overflows horizontally at ${width}px`).toBeLessThanOrEqual(card.clientWidth);
      expect(card.getBoundingClientRect().right).toBeLessThanOrEqual(panelRect.right + 1);

      const hostedRows = Array.from(card.querySelectorAll<HTMLElement>('[data-testid="mobile-fleet-runner"]'));
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
      const heading = root.querySelector<HTMLElement>('[data-testid="mobile-fleet-hostless-heading"]')!;
      expect(heading.getBoundingClientRect().top).toBeGreaterThanOrEqual(card.getBoundingClientRect().bottom);
      const hostless = root.querySelector<HTMLElement>('[data-testid="mobile-fleet-list"] [data-testid="mobile-fleet-runner"]')!;
      expect(hostless.getAttribute('data-runner')).toBe('rn_01KXKVVF1J3D6H6VYZ3XYNHLSS');
      expect(hostless.getBoundingClientRect().top).toBeGreaterThanOrEqual(heading.getBoundingClientRect().bottom);
      expect(hostless.scrollWidth).toBeLessThanOrEqual(hostless.clientWidth);
    } finally {
      root.remove();
    }
  });

  it('animates an online light with the beacon fade and an offline light with the red step blink at 390px', async () => {
    await loadDesignTokens();
    await page.viewport(390, 800);
    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    try {
      const beaconOf = (id: string) =>
        root.querySelector<HTMLElement>(`[data-testid="mobile-fleet-runner"][data-runner="${id}"] fleet-kit-beacon .beacon`)!;
      const online = getComputedStyle(beaconOf('rn_online'));
      expect(online.animationName).toMatch(/beacon-blink$/);
      const offline = getComputedStyle(beaconOf('rn_paused'));
      expect(offline.animationName).toMatch(/beacon-step$/);
      expect(offline.animationDuration).toBe('1.2s');
      expect(online.backgroundColor, 'online is green, offline red').not.toBe(offline.backgroundColor);
      expect(
        root.querySelector('[data-runner="rn_01KXKVVF1J3D6H6VYZ3XYNNEW1"] fleet-kit-beacon'),
        'a never-connected runner keeps its hollow mark, not a beacon',
      ).toBeNull();
    } finally {
      root.remove();
    }
  });
});
