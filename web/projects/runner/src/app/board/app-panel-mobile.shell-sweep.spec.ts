import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { provideRouter } from '@angular/router';
import { runnerClient } from 'fleet';
import { stubRequestClient } from 'fleet/testing';
import { commands, page } from 'vitest/browser';

import { type MachineChunkRow, machineChunkRows } from '../core/chunk-rows.model';
import type { PanelLease } from '../core/panel-reads.model';
import { LocalPanelMobile } from './app-panel-mobile';

/** Matches `GET /runners/rn_1/api/chunks/{chunk_id}/work-items` for any chunk id. */
const WORK_ITEMS_ROUTE = /^\/runners\/rn_1\/api\/chunks\/[^/]+\/work-items$/;

const LEASE = (overrides: Partial<PanelLease> = {}): PanelLease => ({
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
  display_status: 'running',
  closure_healthy: false,
  closed_at: null,
  closure_reason: null,
  stale_after_seconds: 3600,
  resolved_model: null,
  resolved_effort: null,
  harness_version: null,
  git_commits: [],
  harness_id: null,
  project_id: null,
  // A long runner name, so the card's step row has to hold it inside a phone's width too.
  owner: { runnerId: 'rn_1', name: 'r-claude-with-a-long-host-name', label: 'r-claude-with-a-long-host-name' },
  projectSlug: 'blizzard',
  ...overrides,
});

/** A live chunk and one waiting on an ask, tinted by their project — the board's two common card shapes. */
const MACHINE_CHUNKS: MachineChunkRow[] = machineChunkRows(
  [LEASE(), LEASE({ lease_id: 'lease_ask', chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYNASK1', state: 'parked', display_status: 'waiting-ask' })],
  [{ slug: 'blizzard', name: 'Blizzard', entries: ['blizzard'], color: 'cyan' }],
);

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

/**
 * One open ask, shaped the way agents actually write them: paragraph breaks, a numbered
 * list, and — the part narrow width is the only tier that can judge — a bare unbroken
 * repo path far wider than a 320px phone. The ask list preserves these newlines
 * (`app-asks.ts` `.q`), and preserving them is exactly what stops lines breaking on
 * spaces alone, so the long token has to be proven to break rather than push the panel
 * sideways.
 */
const LONG_ASK = {
  items: [
    {
      question_id: 'qn_long',
      chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
      session_id: 'sess-77',
      options: [],
      asked_at: '2026-07-16T11:30:00.000Z',
      question:
        'The reported bug does not reproduce under any condition I can test. Details:\n\n' +
        '1. Code trace: the mutation already invalidates both query keys.\n' +
        '2. Live browser test: the board updated within 500ms.\n\n' +
        'The failing path is web/projects/fleet/src/lib/chunk/chunk-detail/chunk-awaiting-human.spec.ts\n\n' +
        'How should I proceed?',
    },
  ],
};

/** A full `DashboardView` body, `asks` set to `asks` and every other section a
 * plausible/empty default, so the shell's default fallback cannot
 * leave the dashboard readers pending/malformed. */
function dashboardBody(asks: unknown) {
  return {
    runner: {
      runner_id: 'runner-local',
      workspace_id: 'workspace-local',
      pause: { local: false, hub: false, effective: false, local_reason: null },
      capacities: { used: 0, bindings: [{ project: 'p', project_id: 'p', workspace_id: 'ws', agents: 4, used: 0, free: 4 }] },
      hub: { endpoint: 'http://127.0.0.1:8421', reachable: true, last_contact_at: null, buffer_depth: 0, hub_version: null, window: null },
      last_tick_at: null,
    },
    asks,
    escalations: { items: [] },
    takeovers: { items: [] },
    facts: { items: [] },
    harness_health: { items: [] },
    fleet_summary: null,
  };
}

/** The host's one runner, whose dashboard the rail's panels read. */
const HOST_RUNNERS = {
  items: [
    {
      runner_id: 'rn_1',
      name: 'r-claude-with-a-long-host-name',
      state: 'serving',
      served: true,
      halted_reasons: [],
      pause: { local: false, hub: false, effective: false, local_reason: null },
      bindings: [{ project: 'p', workspace_id: 'ws', agents: 4 }],
      agents_in_use: 0,
      max_agents: 4,
    },
  ],
};

async function render(asks: unknown = { items: [] }) {
  const stub = stubRequestClient(runnerClient, (method, path) => {
    if (method === 'GET' && WORK_ITEMS_ROUTE.test(path)) return FIVE_WORK_ITEMS;
    if (method === 'GET' && path === '/api/host/runners') return HOST_RUNNERS;
    if (method === 'GET' && path === '/api/host/environments') return { workspaces: [] };
    if (method === 'GET' && path === '/runners/rn_1/api/dashboard') return dashboardBody(asks);
    return { items: [] };
  });
  await TestBed.configureTestingModule({
    imports: [LocalPanelMobile],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      provideRouter([]),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalPanelMobile);
  const defaults = {
    chunksTriadState: 'ready',
    machineChunks: MACHINE_CHUNKS,
    openAskCount: 0,
  };
  for (const [key, value] of Object.entries(defaults)) fixture.componentRef.setInput(key, value);
  await fixture.whenStable();
  return { fixture, stub };
}

/** The design tokens are a global stylesheet the app build loads, never a standalone mount —
 * injected so the tone colours the chunk cards' badges bind resolve. */
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

// 390 (a typical phone) and 320 (the narrowest common phone) — the widths this
// shell is actually reached at, beneath the persistent mobile bottom tab bar.
const WIDTHS = [390, 320];

describe('runner mobile board shell sweep (web:shell-sweep)', () => {
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

        const card = root.querySelector<HTMLElement>('[data-testid="chunk-row"]');
        expect(card, `width ${width}: no chunk card in the DOM`).not.toBeNull();

        const lines = card!.querySelectorAll<HTMLElement>('[data-testid="chunk-row-title"] .item');
        expect(lines.length, `width ${width}: expected 5 work-item lines`).toBe(5);

        const tops = Array.from(lines).map((line) => line.getBoundingClientRect().top);
        expect(new Set(tops).size, `width ${width}: lines did not stack — tops were ${tops.join(', ')}`).toBe(5);

        // Every card — its worker line and its long-named runner's step row included — stays
        // inside its own box, and the stacked lanes inside the shell.
        for (const each of Array.from(root.querySelectorAll<HTMLElement>('[data-testid="chunk-row"]'))) {
          expect(
            each.scrollWidth,
            `width ${width}: chunk card ${each.dataset['chunkId']} overflows horizontally (${each.scrollWidth} > ${each.clientWidth})`,
          ).toBeLessThanOrEqual(each.clientWidth);
        }
        const lanes = Array.from(root.querySelectorAll<HTMLElement>('[data-testid^="lane-"]'));
        expect(lanes.length, `width ${width}: expected the three lanes`).toBe(3);
        const laneTops = lanes.map((lane) => lane.getBoundingClientRect().top);
        expect(new Set(laneTops).size, `width ${width}: lanes did not stack — tops were ${laneTops.join(', ')}`).toBe(3);
        expect(
          root.scrollWidth,
          `width ${width}: mobile shell overflows horizontally (${root.scrollWidth} > ${root.clientWidth})`,
        ).toBeLessThanOrEqual(root.clientWidth);
      } finally {
        root.remove();
        stub.restore();
        window.removeEventListener('error', onError);
        window.removeEventListener('unhandledrejection', onRejection);
      }

      expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
    });

    it(`renders a multi-paragraph ask on its own lines without overflowing at width ${width}`, async () => {
      const { fixture, stub } = await render(LONG_ASK);
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await fixture.whenStable();

      try {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const ask = root.querySelector<HTMLElement>('[data-testid="ask-row"] .q');
        expect(ask, `width ${width}: no ask text in the DOM`).not.toBeNull();

        // The preserved newlines have to produce real height — a collapsed render of this
        // question still wraps, so height alone is weak. Measuring the same element both
        // ways keeps width, font, and box identical, so the delta is the newlines alone.
        const preserved = ask!.offsetHeight;
        ask!.style.whiteSpace = 'normal';
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const collapsed = ask!.offsetHeight;
        ask!.style.whiteSpace = '';
        await new Promise((resolve) => requestAnimationFrame(resolve));
        expect(
          preserved,
          `width ${width}: ask rendered no taller than its own collapsed rendering (${preserved} vs ${collapsed}) — newlines were not preserved`,
        ).toBeGreaterThan(collapsed);

        // The long unbroken path must break rather than push the panel sideways.
        expect(
          ask!.scrollWidth,
          `width ${width}: ask text overflows horizontally (${ask!.scrollWidth} > ${ask!.clientWidth})`,
        ).toBeLessThanOrEqual(ask!.clientWidth);
        expect(
          root.scrollWidth,
          `width ${width}: mobile shell overflows horizontally (${root.scrollWidth} > ${root.clientWidth})`,
        ).toBeLessThanOrEqual(root.clientWidth);
      } finally {
        root.remove();
        stub.restore();
      }
    });
  }

  it("reads a running worker amber on its card, and a chunk waiting on an ask in the ask's own tone, at width 390", async () => {
    await loadDesignTokens();
    const { fixture, stub } = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const worker = root.querySelector<HTMLElement>('[data-testid="lane-progress"] [data-testid="worker-state"] .badge');
      const askStatus = root.querySelector<HTMLElement>('[data-testid="lane-needs"] [data-testid="chunk-row-status"] .badge');
      expect(worker?.textContent?.trim()).toBe('RUNNING');
      expect(getComputedStyle(worker!).color).toBe(tokenColor('--amber'));
      expect(askStatus?.textContent?.trim()).toBe('WAITING · ASK');
      expect(getComputedStyle(askStatus!).color).not.toBe(getComputedStyle(worker!).color);
    } finally {
      root.remove();
      stub.restore();
    }
  });
});
