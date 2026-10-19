import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { type MachineChunkRow, machineChunkRows } from '../../core/chunk-rows.model';
import type { PanelLease, PanelProject } from '../../core/panel-reads.model';
import { ChunkRow } from './chunk-row';

const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';

/** Matches `GET /runners/{runner_id}/api/chunks/{chunk_id}/work-items`, capturing the runner id. */
const WORK_ITEMS_ROUTE = /^\/runners\/([^/]+)\/api\/chunks\/[^/]+\/work-items$/;

const PROJECTS: PanelProject[] = [
  { slug: 'blizzard', name: 'Blizzard', entries: ['blizzard'], color: 'cyan' },
  { slug: 'ops', name: 'Ops', entries: ['ops'], color: null },
];

const LEASE = (overrides: Partial<PanelLease> = {}): PanelLease => ({
  lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
  chunk_id: CHUNK_ID,
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
  owner: { runnerId: 'rn_1', name: 'r1', label: 'r1' },
  projectSlug: 'blizzard',
  ...overrides,
});

const rowOf = (overrides: Partial<PanelLease> = {}): MachineChunkRow => machineChunkRows([LEASE(overrides)], PROJECTS)[0];

let stub: RequestClientStub | undefined;

afterEach(() => stub?.restore());

async function render(
  workItemsResponse: () => unknown = () => ({ items: [] }),
  inputs: { row?: MachineChunkRow; layout?: 'board' | 'list' } = {},
) {
  stub = stubRequestClient(runnerClient, (method, path) => {
    if (method === 'GET' && WORK_ITEMS_ROUTE.test(path)) return workItemsResponse();
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [ChunkRow],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkRow);
  fixture.componentRef.setInput('row', inputs.row ?? rowOf());
  if (inputs.layout) fixture.componentRef.setInput('layout', inputs.layout);
  await settle(fixture);
  return { fixture, el: fixture.nativeElement as HTMLElement };
}

const WORK_ITEMS = {
  items: [
    {
      source: 'blizzard',
      ref: '61',
      label: 'blizzard#61',
      web_url: 'https://github.com/paul-gross/blizzard/issues/61',
      fetched_at: '2026-07-16T11:00:00.000Z',
      title: 'runner machine panel',
    },
  ],
};

/**
 * `ChunkRow`'s own concerns are the per-row {@link injectChunkTitleQuery} enrichment read —
 * addressed to the runner the row was read off — the project tint it resolves, and which
 * presentation it mounts. The markup belongs to `ChunkRowView` / `ChunkListRowView`.
 */
describe('ChunkRow', () => {
  it('renders on chunk_id alone when the work-items read 502s — never depends on the hub', async () => {
    const { el } = await render(() => stubError(502, { detail: 'stubbed route error (502)' }));

    expect(el.querySelector('[data-testid="chunk-row"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="chunk-row-title"]')).toBeNull();
  });

  it("hands the title query's resolved work items to the view", async () => {
    const { el } = await render(() => WORK_ITEMS);

    expect(el.querySelector('[data-testid="chunk-row-title"]')?.textContent).toContain('blizzard#61');
  });

  it("reads the work items off the row's own runner", async () => {
    await render(() => WORK_ITEMS, { row: rowOf({ owner: { runnerId: 'rn_2', name: 'r2', label: 'r2' } }) });

    expect(stub?.forRoute(`/runners/rn_2/api/chunks/${CHUNK_ID}/work-items`)).toHaveLength(1);
    expect(stub?.forRoute(`/runners/rn_1/api/chunks/${CHUNK_ID}/work-items`)).toHaveLength(0);
  });

  it("resolves the row's project colour into the card's tint", async () => {
    const { el } = await render();
    const card = el.querySelector<HTMLElement>('[data-testid="chunk-row"]')!;

    expect(card.classList).toContain('tinted');
    expect(card.style.getPropertyValue('--project-tint')).toBe('var(--project-cyan, var(--label-dim))');
  });

  it('leaves a row whose project has no colour untinted', async () => {
    const { el } = await render(undefined, { row: rowOf({ projectSlug: 'ops' }) });

    expect(el.querySelector('[data-testid="chunk-row"]')?.classList).not.toContain('tinted');
  });

  it('mounts the list row, tinted the same, for the list layout', async () => {
    const { el } = await render(() => WORK_ITEMS, { layout: 'list' });
    const row = el.querySelector<HTMLElement>('[data-testid="list-row"]');

    expect(el.querySelector('[data-testid="chunk-row"]')).toBeNull();
    expect(row?.classList).toContain('tinted');
    expect(el.querySelector('[data-testid="list-row-title"]')?.textContent).toContain('runner machine panel');
  });
});
