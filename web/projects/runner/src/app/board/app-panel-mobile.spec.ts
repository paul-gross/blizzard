import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { type MachineChunkRow, machineChunkRows } from '../core/chunk-rows.model';
import type { PanelLease } from '../core/panel-reads.model';
import { LocalPanelMobile } from './app-panel-mobile';

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
  harness_id: null,
  project_id: null,
  owner: { runnerId: 'rn_1', name: 'r1', label: 'r1' },
  projectSlug: null,
  ...overrides,
});

const [MACHINE_CHUNK]: MachineChunkRow[] = machineChunkRows([LEASE()]);

async function render(overrides: Record<string, unknown> = {}) {
  await TestBed.configureTestingModule({
    imports: [LocalPanelMobile],
    providers: [
      provideZonelessChangeDetection(),
      // The cards' work-item reads and the rail's panels all need a TanStack Query context.
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      // The dock's chunk name is a `routerLink`, and the rail reads the filters off the URL.
      provideRouter([]),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalPanelMobile);
  const defaults = {
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
    // Only the self-fetching children's own reads need an answer, so every route resolves to the empty shape.
    stub = stubRequestClient(runnerClient, () => ({ items: [] }));
  });

  afterEach(() => stub.restore());

  it('stacks the chunks first, then the rail — hub, host, open asks', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const order = Array.from(el.querySelectorAll('[data-testid="mobile-chunks-pane"], [data-testid^="rail-"]')).map((node) =>
      node.getAttribute('data-testid'),
    );
    expect(order).toEqual(['mobile-chunks-pane', 'rail-hub-pane', 'rail-host-pane', 'rail-asks-pane']);
  });

  it('stacks the board lanes and the rail panels for the single column', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('app-chunk-board')?.classList).toContain('stacked');
    expect(el.querySelector('app-board-rail')?.classList).toContain('stacked');
  });

  it('mounts no local pause control or paused-by-hub badge', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('app-pause-control')).toBeNull();
    expect(el.querySelector('[data-testid="pause-control"]')).toBeNull();
    expect(el.querySelector('[data-testid="hub-paused-badge"]')).toBeNull();
  });

  it('renders one chunk card per chunk, its live worker on it with its heartbeat bar', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const cards = el.querySelectorAll('[data-testid="chunk-row"]');
    expect(cards).toHaveLength(1);
    expect(cards[0].querySelector('[data-testid="chunk-row-worker"] [data-testid="hb-freshness"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="agent-row"]')).toBeNull();
  });

  it('renders the ranked list for the list layout', async () => {
    const fixture = await render({ layout: 'list' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="list-row"]')).toHaveLength(1);
    expect(el.querySelector('[data-testid="chunk-row"]')).toBeNull();
  });

  it('renders the chunks triad state it is handed, without a query of its own', async () => {
    const fixture = await render({ chunksTriadState: 'loading', machineChunks: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-chunks-pane"]')?.textContent).toContain('LOADING…');
  });

  it('renders the chunks empty state when the runners hold no chunks', async () => {
    const fixture = await render({ chunksTriadState: 'empty', machineChunks: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunks-empty"]')?.textContent).toContain('NO CHUNKS ON THESE RUNNERS');
  });

  it('renders the open asks panel off its own query, count in the header note', async () => {
    const fixture = await render({ openAskCount: 3 });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="rail-asks-pane"]')?.textContent).toContain('3 open');
    expect(el.querySelector('[data-testid="local-asks"]')).not.toBeNull();
  });

  it('shows the sections list, not a detail screen, while nothing is selected', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="panel-chunk-detail"]')).toBeNull();
    expect(el.querySelector('[data-testid="mobile-chunks-pane"]')).not.toBeNull();
  });

  it('emits the tapped chunk card id — the container writes the selection', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    const picked: string[] = [];
    fixture.componentInstance.selectChunk.subscribe((id) => picked.push(id));

    el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();

    expect(picked).toEqual([LEASE().chunk_id]);
  });

  it('re-emits the Board/List switch as layoutChange', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    const chosen: string[] = [];
    fixture.componentInstance.layoutChange.subscribe((layout) => chosen.push(layout));

    el.querySelector<HTMLElement>('[data-testid="board-layout-list"]')?.click();

    expect(chosen).toEqual(['list']);
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

    const back = el.querySelector<HTMLButtonElement>('[data-testid="mobile-detail-back"]');
    expect(back?.tagName).toBe('BUTTON');
    expect(back?.getAttribute('aria-label')).toBe('Back to Board');
    back?.click();

    expect(closed).toBe(1);
  });

  it('stays on the list when the selection names a chunk none of the runners hold', async () => {
    const fixture = await render({ selectedChunkLeases: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="panel-chunk-detail"]')).toBeNull();
    expect(el.querySelector('[data-testid="mobile-chunks-pane"]')).not.toBeNull();
  });
});
