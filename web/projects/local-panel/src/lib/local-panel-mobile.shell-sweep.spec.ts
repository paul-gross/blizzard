import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { LocalPanelMobile } from './local-panel-mobile';
import type { MachineChunkRow } from './local-panel';

/** Matches `GET /api/chunks/{chunk_id}/work-items` for any chunk id. */
const WORK_ITEMS_ROUTE = /^\/api\/chunks\/[^/]+\/work-items$/;

const LEASE = (overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView => ({
  lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  graph_id: 'gr_1',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 2,
  session_id: 'sess-77',
  pid: 4821,
  environment_id: 'beta',
  workdir: '/ws/beta',
  created_at: '2026-07-16T11:00:00.000Z',
  last_heartbeat_at: '2026-07-16T11:59:26.000Z',
  state: 'running',
  closed_at: null,
  closure_reason: null,
  ...overrides,
});

const MACHINE_CHUNK: MachineChunkRow = { lease: LEASE(), leases: [LEASE()], status: { label: 'RUNNING', tone: 'running' } };

/** Five work items — enough to prove the lines genuinely stack rather than reflow. */
const FIVE_WORK_ITEMS = {
  items: Array.from({ length: 5 }, (_, i) => ({
    source: 'blizzard',
    ref: String(160 + i),
    label: `blizzard#${160 + i}`,
    web_url: `https://github.com/paul-gross/blizzard/issues/${160 + i}`,
    fetched_at: '2026-07-16T11:00:00.000Z',
    title: `work item title number ${i + 1} — long enough to wrap across two clamped lines on a narrow phone`,
  })),
};

async function render() {
  const stub = stubRequestClient(runnerClient, (method, path) => {
    if (method === 'GET' && WORK_ITEMS_ROUTE.test(path)) return FIVE_WORK_ITEMS;
    return { items: [] };
  });
  await TestBed.configureTestingModule({
    imports: [LocalPanelMobile],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalPanelMobile);
  const defaults = {
    activeLeases: [LEASE()],
    leasesTriadState: 'ready',
    chunksTriadState: 'ready',
    machineChunks: [MACHINE_CHUNK],
    openAskCount: 0,
  };
  for (const [key, value] of Object.entries(defaults)) fixture.componentRef.setInput(key, value);
  await fixture.whenStable();
  return { fixture, stub };
}

// 390 (a typical phone) and 320 (the narrowest common phone) — the widths this
// shell is actually reached at, beneath the persistent mobile bottom tab bar.
const WIDTHS = [390, 320];

describe('runner mobile chunk list shell sweep (web:shell-sweep, issue #176)', () => {
  for (const width of WIDTHS) {
    it(`stacks a five-work-item chunk card's lines with no horizontal overflow at width ${width}`, async () => {
      const pageErrors: string[] = [];
      const onError = (e: ErrorEvent) => pageErrors.push(e.message);
      const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
      window.addEventListener('error', onError);
      window.addEventListener('unhandledrejection', onRejection);

      const { fixture, stub } = await render();
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await fixture.whenStable();

      try {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const card = root.querySelector<HTMLElement>('[data-testid="local-chunk-card"]');
        expect(card, `width ${width}: no chunk card in the DOM`).not.toBeNull();

        const lines = card!.querySelectorAll<HTMLElement>('.wi');
        expect(lines.length, `width ${width}: expected 5 work-item lines`).toBe(5);

        const tops = Array.from(lines).map((line) => line.getBoundingClientRect().top);
        expect(new Set(tops).size, `width ${width}: lines did not stack — tops were ${tops.join(', ')}`).toBe(5);

        expect(
          card!.scrollWidth,
          `width ${width}: chunk card overflows horizontally (${card!.scrollWidth} > ${card!.clientWidth})`,
        ).toBeLessThanOrEqual(card!.clientWidth);
      } finally {
        root.remove();
        stub.restore();
        window.removeEventListener('error', onError);
        window.removeEventListener('unhandledrejection', onRejection);
      }

      expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
    });
  }
});
