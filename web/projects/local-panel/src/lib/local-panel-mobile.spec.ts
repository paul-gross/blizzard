import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
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
      // LocalPanelMobile renders `ChunkCard` and `LocalAsks`/`LocalInfo`, all of
      // whose own reads need a TanStack Query context to construct at all.
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      // The detail screen's header links the chunk name to its route now (issue #318).
      provideRouter([]),
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

  it('shows the sections list, not a detail screen, while nothing is selected', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="panel-chunk-detail"]')).toBeNull();
    expect(el.querySelector('[data-testid="transcript-panel"]')).toBeNull();
    expect(el.querySelector('[data-testid="mobile-chunks-pane"]')).not.toBeNull();
  });

  it('emits the tapped chunk card id — the container writes the selection', async () => {
    const fixture = await render({ machineChunks: [MACHINE_CHUNK] });
    const el = fixture.nativeElement as HTMLElement;
    const picked: string[] = [];
    fixture.componentInstance.selectChunk.subscribe((id) => picked.push(id));

    el.querySelector<HTMLElement>('[data-testid="local-chunk-card"]')?.click();

    expect(picked).toEqual([LEASE().chunk_id]);
  });

  it('emits the tapped agent row lease id — a lease tap selects its chunk too', async () => {
    const fixture = await render({ activeLeases: [LEASE()] });
    const el = fixture.nativeElement as HTMLElement;
    const picked: string[] = [];
    fixture.componentInstance.selectLease.subscribe((id) => picked.push(id));

    el.querySelector<HTMLElement>('[data-testid="agent-row"]')?.click();

    expect(picked).toEqual([LEASE().lease_id]);
  });

  it('drills down to the chunk detail screen once the container resolves a selection', async () => {
    const fixture = await render({ selectedChunkLeases: [LEASE()], selectedStatus: { label: 'RUNNING', tone: 'running' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="panel-chunk-detail"]')).not.toBeNull();
    // The desktop dock reused verbatim — its execution facts.
    const facts = el.querySelector('[data-testid="detail-facts"]')?.textContent ?? '';
    expect(facts).toContain('sess-77');
    expect(facts).toContain('4821');
    expect(facts).toContain('beta');
    expect(facts).toContain('/ws/beta');
    // …in place of the sections list, not beside it.
    expect(el.querySelector('[data-testid="mobile-chunks-pane"]')).toBeNull();
  });

  it('emits closeDetail from the back affordance — the container clears the selection', async () => {
    const fixture = await render({ selectedChunkLeases: [LEASE()] });
    const el = fixture.nativeElement as HTMLElement;
    let closed = 0;
    fixture.componentInstance.closeDetail.subscribe(() => (closed += 1));

    el.querySelector<HTMLElement>('[data-testid="mobile-detail-back"]')?.click();

    expect(closed).toBe(1);
  });

  it('stays on the list when the selection names a chunk this machine does not hold', async () => {
    const fixture = await render({ selectedChunkLeases: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="panel-chunk-detail"]')).toBeNull();
    expect(el.querySelector('[data-testid="mobile-chunks-pane"]')).not.toBeNull();
  });
});
