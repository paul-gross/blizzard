import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { commands, page } from 'vitest/browser';

import { firstFleetId, fleetList, resolveSelection } from './fleet-page.model';
import { FleetView } from './fleet-view';
import { type HostCard, groupHostRows } from './host-rows.model';
import { NO_HOST_VERBS } from './host-verbs.model';
import type { RunnerRow } from './runner-rows';

/**
 * The desktop Fleet tab (the tooled half of `blizzard-context:/verification/blizzard.md`'s
 * `web:shell-sweep` method) — a real, headless-Chromium proof that the host list and the selected
 * host's detail sit side by side at 1280px, the title-bar actions are on screen and hit-testable,
 * the detail pane scrolls inside itself, and nothing overflows horizontally at 1280px or 1024px.
 * jsdom never lays out the split, so this is a claim `web:unit-test` cannot make good on
 * (`bzh:visual-change-needs-a-render`).
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

const LONG = 'workspace-with-a-genuinely-long-operator-chosen-identifier';
const card = (id: string, name: string, over: Partial<HostCard> = {}): HostCard => ({
  hostId: id,
  hostName: name,
  online: true,
  connection: 'online',
  reportLabel: 'seen 4s ago',
  url: 'https://build-host.example.com:8431',
  enrolled: true,
  registeredAt: NOW,
  addedAt: NOW,
  addedBy: 'paul',
  lastSeenLabel: 'now',
  heldChunks: new Map([
    [LONG, [{ chunkId: 'ch_1', shortRef: 'C-0001', step: 'build', runnerName: 'runner-alpha', environments: 1 }]],
  ]),
  workspaces: [{ workspaceId: LONG, capacity: 6, inUse: 1, flag: null, environments: [] }],
  subscriptionPaces: [],
  ...over,
});
const CARDS = [
  card('hs_01KXKVVF1J3D6H6VYZ3XYNONLN', 'build-host-with-a-genuinely-long-operator-chosen-name'),
  card('hs_01KXKVVF1J3D6H6VYZ3XYNOFFL', 'offline-box', { online: false, connection: 'offline' }),
];
const row = (id: string, name: string, hostId: string | null): RunnerRow => ({
  ...ROW,
  runner_id: id,
  runner_name: name,
  host_id: hostId,
  bindingLines: hostId ? [{ project: 'Blizzard', workspaceId: LONG, agents: 3, agentsInUse: 1, unreported: false }] : [],
});
const ROWS = [
  row('rn_01KXKVVF1J3D6H6VYZ3XYNALFA', 'runner-alpha', CARDS[0].hostId),
  row('rn_01KXKVVF1J3D6H6VYZ3XYNBRAV', 'runner-bravo', CARDS[0].hostId),
  row('rn_01KXKVVF1J3D6H6VYZ3XYNSOLO', 'solo', null),
];

describe('desktop Fleet tab layout shell sweep (web:shell-sweep)', () => {
  it.each([1280, 1024])('lays the split out without horizontal overflow at %ipx', async (width) => {
    await loadDesignTokens();
    await TestBed.configureTestingModule({
      imports: [FleetView],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const groups = groupHostRows(ROWS, CARDS);
    const list = fleetList(groups);
    const fixture = TestBed.createComponent(FleetView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('list', list);
    fixture.componentRef.setInput('selectedId', firstFleetId(list));
    fixture.componentRef.setInput('selection', resolveSelection(groups, firstFleetId(list)));
    fixture.componentRef.setInput('canPause', true);
    fixture.componentRef.setInput('hostVerbs', { ...NO_HOST_VERBS, canRetire: true });
    const root = fixture.nativeElement as HTMLElement;
    root.style.display = 'block';
    root.style.height = '700px';
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 900);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const listPane = root.querySelector<HTMLElement>('[data-testid="fleet-list"]')!;
      const detail = root.querySelector<HTMLElement>('[data-testid="fleet-detail"]')!;
      expect(detail.getBoundingClientRect().left, 'detail sits beside the list').toBeGreaterThanOrEqual(
        listPane.getBoundingClientRect().right - 1,
      );
      expect(root.scrollWidth, `page overflows horizontally at ${width}px`).toBeLessThanOrEqual(root.clientWidth);
      expect(detail.scrollWidth, `detail overflows horizontally at ${width}px`).toBeLessThanOrEqual(detail.clientWidth);

      for (const testid of ['host-enroll', 'host-revoke-token', 'host-retire']) {
        const button = root.querySelector<HTMLElement>(`[data-testid="${testid}"]`)!;
        const rect = button.getBoundingClientRect();
        expect(rect.right, `${testid} is off screen at ${width}px`).toBeLessThanOrEqual(window.innerWidth);
        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        expect(button.contains(hit), `${testid} is not hit-testable at ${width}px`).toBe(true);
      }

      expect(root.querySelectorAll('[data-testid="fleet-runner"]')).toHaveLength(2);
      expect(root.querySelector('[data-testid="fleet-hostless-heading"]')).not.toBeNull();
    } finally {
      root.remove();
    }
  });

  it.each([1280, 1024])('seats a selected host-less runner\'s slot bar and pace bars inside the detail at %ipx', async (width) => {
    await loadDesignTokens();
    await TestBed.configureTestingModule({
      imports: [FleetView],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const pace = (slug: string, freshness: 'aging' | 'stale', label: string) => ({
      slug,
      name: `${slug} — a genuinely long subscription display name`,
      condition: null,
      paceBars: [
        { window: '5h', utilizationPct: 40, elapsedPct: 20 },
        { window: '7d', utilizationPct: 70, elapsedPct: 55 },
      ],
      sampledAt: NOW,
      refreshedLabel: label,
      freshness,
      missReason: null,
    });
    const solo: RunnerRow = {
      ...row('rn_01KXKVVF1J3D6H6VYZ3XYNSOLO', 'solo', null),
      subscriptionPaces: [pace('aging', 'aging', 'refreshed 30m ago'), pace('stale', 'stale', 'refreshed 2h ago')],
    };
    const groups = groupHostRows([solo], []);
    const list = fleetList(groups);
    const fixture = TestBed.createComponent(FleetView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('list', list);
    fixture.componentRef.setInput('selectedId', solo.runner_id);
    fixture.componentRef.setInput('selection', resolveSelection(groups, solo.runner_id));
    fixture.componentRef.setInput('canPause', true);
    const root = fixture.nativeElement as HTMLElement;
    root.style.display = 'block';
    root.style.height = '700px';
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 900);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const detail = root.querySelector<HTMLElement>('[data-testid="fleet-detail"]')!;
      const bar = root.querySelector<HTMLElement>('[data-testid="fleet-runner-slot-bar"]')!;
      expect(bar.getBoundingClientRect().right, 'slot bar is past the detail').toBeLessThanOrEqual(detail.getBoundingClientRect().right + 1);
      const bars = root.querySelectorAll<HTMLElement>('[data-testid="fleet-runner-subscription-groups"] [data-testid="runner-pace-bar"]');
      expect(bars).toHaveLength(4);
      for (const b of bars) {
        expect(b.getBoundingClientRect().right, 'a pace bar is past the detail').toBeLessThanOrEqual(detail.getBoundingClientRect().right + 1);
      }
      expect(detail.scrollWidth, `detail overflows horizontally at ${width}px`).toBeLessThanOrEqual(detail.clientWidth);

      const aging = root.querySelector<HTMLElement>('[data-subscription-slug="aging"] [data-testid="subscription-pace-group-refreshed"]')!;
      const stale = root.querySelector<HTMLElement>('[data-subscription-slug="stale"] [data-testid="subscription-pace-group-refreshed"]')!;
      expect(getComputedStyle(aging).color).not.toBe(getComputedStyle(stale).color);
      expect(getComputedStyle(aging).color).not.toBe(getComputedStyle(root).color);
    } finally {
      root.remove();
    }
  });
});
