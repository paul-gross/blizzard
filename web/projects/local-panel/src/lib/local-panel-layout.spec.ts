import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, stubRequestClient } from 'fleet/testing';

import { LocalPanelLayout } from './local-panel-layout';
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
    imports: [LocalPanelLayout],
    providers: [
      provideZonelessChangeDetection(),
      // `LocalPanelLayout` itself is presentational, but it composes several
      // self-fetching mini-containers that inject their own queries/mutations —
      // `ChunkRow`'s severable work-item-title read (issue #28, decision 1) —
      // so a TanStack Query context has to exist for the fixture to construct
      // at all.
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      // The detail dock's header links the chunk name to its route now (issue #318).
      provideRouter([]),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalPanelLayout);
  const defaults = {
    activeLeases: [LEASE()],
    leasesTriadState: 'ready',
    chunksTriadState: 'ready',
    chunksEmptyText: 'NO CHUNKS ON THIS MACHINE',
    machineChunks: [MACHINE_CHUNK],
    showAllChunks: false,
    openAskCount: 0,
    selectedChunkId: null,
    selectedChunkLeases: [],
    selectedStatus: null,
    selectedEscalation: null,
    ...overrides,
  };
  for (const [key, value] of Object.entries(defaults)) fixture.componentRef.setInput(key, value);
  await fixture.whenStable();
  return fixture;
}

describe('LocalPanelLayout', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    // `ChunkRow`'s own work-item-title lookup just needs an answer, not a
    // realistic one, so every route resolves to this one empty shape.
    stub = stubRequestClient(runnerClient, () => ({ items: [] }));
  });

  afterEach(() => stub.restore());

  it('renders the async triad state it is handed, without a query of its own', async () => {
    const fixture = await render({ leasesTriadState: 'loading', activeLeases: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="loading-state"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
  });

  it('renders one agent-row per active lease', async () => {
    const fixture = await render({ activeLeases: [LEASE()] });
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="agent-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute('data-lease-id')).toBe('lease_01KXKVVF1J3D6H6VYZ3XYNZPRR');
  });

  it('emits selectLease when an agent row is activated', async () => {
    const fixture = await render({ activeLeases: [LEASE()] });
    let selected: string | undefined;
    fixture.componentInstance.selectLease.subscribe((id) => (selected = id));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="agent-row"]')?.click();
    expect(selected).toBe('lease_01KXKVVF1J3D6H6VYZ3XYNZPRR');
  });

  it('renders one chunk-row per machine chunk and marks the selected one', async () => {
    const fixture = await render({ machineChunks: [MACHINE_CHUNK], selectedChunkId: MACHINE_CHUNK.lease.chunk_id });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="chunk-row"]')).toHaveLength(1);
    expect(el.querySelector('[data-testid="chunk-row"]')?.classList.contains('selected')).toBe(true);
  });

  it("wires the chunk row's lease/status inputs through to ChunkRow — content itself is ChunkRow's own spec (issue #134)", async () => {
    const fixture = await render({ machineChunks: [MACHINE_CHUNK] });
    const el = fixture.nativeElement as HTMLElement;
    const card = el.querySelector<HTMLElement>('[data-testid="chunk-row"]');

    expect(card?.getAttribute('data-chunk-id')).toBe(MACHINE_CHUNK.lease.chunk_id);
    // The lane-colored left edge — proof the `status` input itself (not just
    // the chunk id) reached the child, the derived status's own tone color.
    expect(card?.style.borderLeftColor).toBe('var(--amber)');
  });

  it('renders the "show all" checkbox bar above the chunks list, reflecting the input and emitting on toggle', async () => {
    const fixture = await render({ showAllChunks: false });
    let toggled: boolean | undefined;
    fixture.componentInstance.toggleShowAllChunks.subscribe((checked) => (toggled = checked));
    const el = fixture.nativeElement as HTMLElement;
    const checkbox = el.querySelector<HTMLInputElement>('[data-testid="chunk-filter-show-all"]');

    expect(checkbox?.checked).toBe(false);

    checkbox?.click();
    expect(toggled).toBe(true);
  });

  it('reflects a checked "show all" input and emits false on uncheck', async () => {
    const fixture = await render({ showAllChunks: true });
    let toggled: boolean | undefined;
    fixture.componentInstance.toggleShowAllChunks.subscribe((checked) => (toggled = checked));
    const el = fixture.nativeElement as HTMLElement;
    const checkbox = el.querySelector<HTMLInputElement>('[data-testid="chunk-filter-show-all"]');

    expect(checkbox?.checked).toBe(true);

    checkbox?.click();
    expect(toggled).toBe(false);
  });

  it('emits selectChunk when a chunk row is activated', async () => {
    const fixture = await render({ machineChunks: [MACHINE_CHUNK] });
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();
    expect(selected).toBe(MACHINE_CHUNK.lease.chunk_id);
  });

  it('shows the SELECT A CHUNK placeholder in the detail dock before anything is selected', async () => {
    const fixture = await render({ selectedChunkLeases: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detail-empty"]')?.textContent).toContain('SELECT A CHUNK');
  });

  it('renders the selected chunk in the detail dock, summary off the newest attempt', async () => {
    const fixture = await render({ selectedChunkLeases: [LEASE()], selectedStatus: MACHINE_CHUNK.status });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toContain('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
  });
});
