import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { LocalPanelMobile } from './local-panel-mobile';
import type { MachineChunkRow } from './local-panel';

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

async function render(overrides: Record<string, unknown> = {}) {
  await TestBed.configureTestingModule({
    imports: [LocalPanelMobile],
    providers: [
      provideZonelessChangeDetection(),
      // LocalPanelMobile now injects its own runner-status read (the titlebar's
      // `live` dot) plus renders `ChunkCard` and `LocalAsks`/`LocalInfo`, all of
      // whose own reads need a TanStack Query context to construct at all.
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
    ...overrides,
  };
  for (const [key, value] of Object.entries(defaults)) fixture.componentRef.setInput(key, value);
  await settle(fixture);
  return fixture;
}

describe('LocalPanelMobile', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    // Same reason as the provider above: only the self-contained children's own
    // reads need an answer, so every route resolves to the empty shape.
    stub = stubRequestClient(runnerClient, () => ({ items: [] }));
  });

  afterEach(() => stub.restore());

  it('stacks the four sections in attention order — info, agents, chunks, asks', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const panes = ['mobile-info-pane', 'mobile-agents-pane', 'mobile-chunks-pane', 'mobile-asks-pane'].map(
      (testid) => el.querySelector(`[data-testid="${testid}"]`),
    );
    expect(panes.every((pane) => pane !== null)).toBe(true);

    const order = Array.from(el.querySelectorAll('[data-testid$="-pane"]')).map((node) =>
      node.getAttribute('data-testid'),
    );
    expect(order).toEqual(['mobile-info-pane', 'mobile-agents-pane', 'mobile-chunks-pane', 'mobile-asks-pane']);
  });

  it('renders the machine info section off its own query, no props needed', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-info"]')).not.toBeNull();
  });

  it('renders one agent row per active lease, with its heartbeat-freshness bar', async () => {
    const fixture = await render({ activeLeases: [LEASE()] });
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="agent-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].querySelector('[data-testid="hb-freshness"]')).not.toBeNull();
  });

  it('renders the leases triad state it is handed, without a query of its own', async () => {
    const fixture = await render({ leasesTriadState: 'loading', activeLeases: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="loading-state"]')).not.toBeNull();
  });

  it('renders one chunk card per machine chunk', async () => {
    const fixture = await render({ machineChunks: [MACHINE_CHUNK] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="local-chunk-card"]')).toHaveLength(1);
  });

  it('renders the chunks empty state when the machine holds no chunks', async () => {
    const fixture = await render({ chunksTriadState: 'empty', machineChunks: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunks-empty"]')?.textContent).toContain('NO CHUNKS ON THIS MACHINE');
  });

  it('renders the local asks section off its own query, count in the header note', async () => {
    const fixture = await render({ openAskCount: 3 });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-asks-pane"]')?.textContent).toContain('3 open');
    expect(el.querySelector('[data-testid="local-asks"]')).not.toBeNull();
  });

  it('never renders the transcript panel or a detail dock — out of scope for this shell', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="transcript-panel"]')).toBeNull();
    expect(el.querySelector('[data-testid="detail-empty"]')).toBeNull();
  });

  it('clicking a chunk card is inert — no selection/dock output to react to it', async () => {
    const fixture = await render({ machineChunks: [MACHINE_CHUNK] });
    const el = fixture.nativeElement as HTMLElement;

    expect(() => el.querySelector<HTMLElement>('[data-testid="local-chunk-card"]')?.click()).not.toThrow();
    expect(el.querySelector('[data-testid="local-chunk-card"]')?.classList.contains('selected')).toBe(false);
  });

  it('renders the shared mobile titlebar with its own menu slot, closed by default', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-panel-mobile-titlebar"]')).not.toBeNull();
    expect(el.querySelector('fleet-viewport-toggle')).toBeNull();

    el.querySelector<HTMLElement>('[data-testid="local-panel-mobile-titlebar-menu"]')?.click();
    await fixture.whenStable();

    expect(
      el.querySelector('[data-testid="local-panel-mobile-titlebar-menu-panel"] fleet-viewport-toggle'),
    ).not.toBeNull();
  });

  it('derives the titlebar live dot from the runner status hub-reachable read', async () => {
    stub.restore();
    stub = stubRequestClient(runnerClient, (method, path) => {
      if (method === 'GET' && path === '/api/runner') {
        return {
          hub: {
            endpoint: 'http://127.0.0.1:8421',
            reachable: true,
            last_contact_at: null,
            buffer_depth: 0,
          },
          capacities: { used: 0, max_agents: 4 },
          pause: { effective: false },
          last_tick_at: null,
        };
      }
      return { items: [] };
    });
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(
      el.querySelector('[data-testid="local-panel-mobile-titlebar-livedot"]')?.classList.contains('active'),
    ).toBe(true);
  });
});
