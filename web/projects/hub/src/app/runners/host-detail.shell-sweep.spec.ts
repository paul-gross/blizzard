import { RunnerConnection, type RunnerRegistryView } from 'fleet';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { commands, page } from 'vitest/browser';

import { type HostDetailVm, resolveSelection } from './fleet-page.model';
import { HostDetail } from './host-detail';
import type { HostCard } from './host-rows.model';
import type { RunnerRow } from './runner-rows.model';

const REGISTRY: RunnerRegistryView = {
  agents: null,
  runner_id: '',
  runner_name: '',
  added_at: '',
  added_by: null,
  bindings: null,
  capabilities: [],
  connection: RunnerConnection.NEVER_CONNECTED,
  declining: [],
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

/** The design tokens are a global stylesheet, never loaded by a standalone component test —
 * inject the sheet's real text so the sections lay out at their real sizes. */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

/**
 * The host detail at phone width — the phone's Fleet tab opens it full-screen under `/fleet/:id`
 * — the tooled half of `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method: a
 * real, headless-Chromium proof that the detail column's own `@container fleet-detail` breakpoint
 * stacks its connection facts, its environment lines and each runner's facts over its held
 * chunks, with no horizontal overflow at 390/320px, and that each held chunk is a finger-sized
 * link. jsdom never evaluates a `@container` rule, so this is a claim `web:unit-test` cannot make
 * good on (`bzh:narrow-viewport-tier-rule`).
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) because it needs
 * `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const NOW = new Date().toISOString();
const WORKSPACE = 'workspace-with-a-long-operator-chosen-name';
const RUNNER_NAME = 'runner-with-a-genuinely-long-operator-chosen-name';

const CLAIMS = [
  { chunkId: 'ch_01KXKVVF1J3D6H6VYZ3XYNCL01', shortId: 'C-CL01', node: 'build', status: 'running' as const },
  { chunkId: 'ch_01KXKVVF1J3D6H6VYZ3XYNCL02', shortId: 'C-CL02', node: 'deliver', status: 'delivering' as const },
];

const ROW: RunnerRow = {
  ...REGISTRY,
  runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3',
  runner_name: RUNNER_NAME,
  added_at: NOW,
  connection: 'online',
  workspace_id: WORKSPACE,
  registered_at: NOW,
  last_seen_at: NOW,
  online: true,
  host_id: 'hs_1',
  nowMs: Date.parse(NOW),
  served: [],
  unassigned: false,
  unresolved: [],
  bindingLines: [{ project: 'Blizzard', workspaceId: WORKSPACE, agents: 6, agentsInUse: 2, unreported: false }],
  subscriptionName: null,
  claims: CLAIMS,
  used: 2,
  subscriptionPaces: [],
};

const CARD: HostCard = {
  hostId: 'hs_1',
  hostName: 'build-box-with-a-long-hostname',
  online: true,
  connection: RunnerConnection.ONLINE,
  reportLabel: 'seen 4s ago',
  url: 'https://build-box-with-a-long-hostname.example.internal:8443',
  enrolled: true,
  registeredAt: '2026-01-02T03:04:05Z',
  addedAt: '2026-01-01T00:00:00Z',
  addedBy: 'operator',
  lastSeenLabel: '2026/10/22 20:39:19',
  reportedAgo: '4s ago',
  workspaces: [{ workspaceId: WORKSPACE, capacity: 3, inUse: 2, flag: null, environments: [] }],
  heldChunks: new Map([
    [
      WORKSPACE,
      CLAIMS.map((claim) => ({ chunkId: claim.chunkId, shortRef: claim.shortId, step: claim.node, runnerName: RUNNER_NAME, environments: 1 })),
    ],
  ]),
  subscriptionPaces: [],
};

function detail(): HostDetailVm {
  const sel = resolveSelection({ cards: [{ card: CARD, rows: [ROW] }], hostless: [] }, CARD.hostId);
  if (sel?.kind !== 'host') throw new Error('expected a host');
  return sel;
}

async function render() {
  await TestBed.configureTestingModule({
    imports: [HostDetail],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(HostDetail);
  fixture.componentRef.setInput('detail', detail());
  fixture.componentRef.setInput('canPause', true);
  await fixture.whenStable();
  return fixture;
}

describe('host detail at phone width shell sweep (web:shell-sweep)', () => {
  it.each([390, 320])('stacks every section inside the column with no horizontal overflow at %ipx', async (width) => {
    await loadDesignTokens();
    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 900);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      expect(root.scrollWidth, `${width}px: the detail overflows horizontally`).toBeLessThanOrEqual(root.clientWidth);

      // The connection's two fact lists stack rather than share a row.
      const lists = root.querySelectorAll<HTMLElement>('[data-testid="fleet-connection"] fleet-kit-fact-list');
      expect(lists).toHaveLength(2);
      expect(lists[1].getBoundingClientRect().top, `${width}px: the connection facts share a row`).toBeGreaterThanOrEqual(
        lists[0].getBoundingClientRect().bottom - 1,
      );

      // Each held environment line stays inside its section.
      const section = root.querySelector<HTMLElement>('[data-testid="fleet-workspaces"]')!.getBoundingClientRect();
      const held = root.querySelectorAll<HTMLElement>('[data-testid="fleet-env-held"]');
      expect(held).toHaveLength(2);
      for (const line of held) {
        expect(line.getBoundingClientRect().right, `${width}px: an environment line overflows`).toBeLessThanOrEqual(section.right + 1);
        expect(line.scrollWidth, `${width}px: an environment line overflows`).toBeLessThanOrEqual(line.clientWidth);
      }

      // A runner's facts sit above its held chunks, each chunk a finger-sized link to its page.
      const runner = root.querySelector<HTMLElement>('[data-testid="fleet-runner"]')!;
      const facts = runner.querySelector<HTMLElement>('.facts')!.getBoundingClientRect();
      const claims = runner.querySelector<HTMLElement>('.held')!.getBoundingClientRect();
      expect(claims.top, `${width}px: the held chunks sit beside the facts, not under them`).toBeGreaterThanOrEqual(facts.bottom - 1);
      const links = runner.querySelectorAll<HTMLElement>('[data-testid="fleet-runner-claim-link"]');
      expect(links).toHaveLength(2);
      for (const link of links) {
        const box = link.getBoundingClientRect();
        expect(box.height, `${width}px: a held chunk is not finger-sized`).toBeGreaterThanOrEqual(32);
        expect(box.right, `${width}px: a held chunk overflows its runner`).toBeLessThanOrEqual(runner.getBoundingClientRect().right + 1);
      }
    } finally {
      root.remove();
    }
  });
});
