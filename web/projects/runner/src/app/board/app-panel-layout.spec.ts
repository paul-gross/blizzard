import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { type MachineChunkRow, machineChunkRows } from '../core/chunk-rows.model';
import type { PanelLease } from '../core/panel-reads.model';
import { LocalPanelLayout } from './app-panel-layout';
import type { BoardLayout } from './chunk-list/chunk-board';

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
    imports: [LocalPanelLayout],
    providers: [
      provideZonelessChangeDetection(),
      // `LocalPanelLayout` itself is presentational, but it composes self-fetching
      // containers — each card's work-item read, the rail's hub, host and asks panels —
      // so a TanStack Query context has to exist for the fixture to construct at all.
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      // The dock's chunk name is a `routerLink`, and the rail reads the filters off the URL.
      provideRouter([]),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalPanelLayout);
  const defaults = {
    chunksTriadState: 'ready',
    machineChunks: [MACHINE_CHUNK],
    openAskCount: 0,
    selectedChunkId: null,
    selectedChunkLeases: [],
    selectedStatus: null,
    selectedEscalation: null,
    ...overrides,
  };
  for (const [key, value] of Object.entries(defaults)) fixture.componentRef.setInput(key, value);
  await settle(fixture);
  return fixture;
}

describe('LocalPanelLayout', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    // The self-fetching children just need an answer, not a realistic one.
    stub = stubRequestClient(runnerClient, () => ({ items: [] }));
  });

  afterEach(() => stub.restore());

  it('renders the async triad state it is handed, without a query of its own', async () => {
    const fixture = await render({ chunksTriadState: 'loading', machineChunks: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunks-pane"]')?.textContent).toContain('LOADING…');
    expect(el.querySelector('[data-testid="chunks-empty"]')).toBeNull();
  });

  it('lays the chunks out as lanes, one card per chunk, and marks the selected one', async () => {
    const fixture = await render({ selectedChunkId: MACHINE_CHUNK.lease.chunk_id });
    const el = fixture.nativeElement as HTMLElement;

    const cards = el.querySelectorAll('[data-testid="lane-progress"] [data-testid="chunk-row"]');
    expect(cards).toHaveLength(1);
    expect(cards[0].getAttribute('data-chunk-id')).toBe(MACHINE_CHUNK.lease.chunk_id);
    expect(cards[0].classList).toContain('selected');
    expect(el.querySelector('[data-testid="agent-row"]')).toBeNull();
    expect(el.querySelector('[data-testid="chunk-filter-show-all"]')).toBeNull();
  });

  it('renders the ranked list for the list layout', async () => {
    const fixture = await render({ layout: 'list' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="board-list"] [data-testid="list-row"]')).toHaveLength(1);
  });

  it('re-emits a picked chunk as selectChunk', async () => {
    const fixture = await render();
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();
    expect(selected).toBe(MACHINE_CHUNK.lease.chunk_id);
  });

  it('re-emits the Board/List switch as layoutChange', async () => {
    const fixture = await render();
    const chosen: BoardLayout[] = [];
    fixture.componentInstance.layoutChange.subscribe((layout) => chosen.push(layout));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="board-layout-list"]')?.click();
    expect(chosen).toEqual(['list']);
  });

  it('closes the detail dock — taking no room — while nothing is selected', async () => {
    const fixture = await render({ selectedChunkLeases: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('app-machine-detail')?.classList).toContain('closed');
    expect(el.querySelector('[data-testid="detail-empty"]')?.textContent).toContain('SELECT A CHUNK');
  });

  it('opens the selected chunk in the detail dock, summary off the newest attempt', async () => {
    const fixture = await render({ selectedChunkLeases: [LEASE()], selectedStatus: MACHINE_CHUNK.status });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('app-machine-detail')?.classList).not.toContain('closed');
    expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toContain('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
  });

  it('emits dismiss from the dock close button', async () => {
    const fixture = await render({ selectedChunkLeases: [LEASE()] });
    let dismissed = 0;
    fixture.componentInstance.dismiss.subscribe(() => (dismissed += 1));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="detail-close"]')?.click();
    expect(dismissed).toBe(1);
  });

  it('renders the rail — hub, host, then open asks, the count in its header note — in the right column', async () => {
    const fixture = await render({ openAskCount: 2 });
    const el = fixture.nativeElement as HTMLElement;
    const right = el.querySelector('.col.right');

    const panes = Array.from(right?.querySelectorAll('[data-testid^="rail-"]') ?? []).map((pane) => pane.getAttribute('data-testid'));
    expect(panes).toEqual(['rail-hub-pane', 'rail-host-pane', 'rail-asks-pane']);
    expect(right?.querySelector('[data-testid="rail-asks-pane"]')?.textContent).toContain('2 open');
    expect(el.querySelector('.col.center [data-testid="chunks-pane"]')).not.toBeNull();
  });
});
