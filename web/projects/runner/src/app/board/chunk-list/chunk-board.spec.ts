import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type KitAsyncStateValue, runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { type MachineChunkRow, machineChunkRows } from '../../core/chunk-rows.model';
import type { PanelLease } from '../../core/panel-reads.model';
import { type BoardLayout, ChunkBoard } from './chunk-board';

const LEASE = (overrides: Partial<PanelLease> = {}): PanelLease => ({
  lease_id: 'lease_1',
  chunk_id: 'ch_1',
  graph_id: 'gr_1',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 1,
  session_id: 'sess-1',
  pid: 1,
  environment_id: 'e1',
  workdir: '/ws/e1',
  created_at: '2026-07-16T11:00:00.000Z',
  last_heartbeat_at: '2026-07-16T11:59:00.000Z',
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

/** One chunk per lane, in the merged order the container folds — done first, so the list's
 * lane ranking is visible. */
const ROWS: readonly MachineChunkRow[] = machineChunkRows([
  LEASE({ chunk_id: 'ch_done', lease_id: 'l_done', state: 'closed', display_status: 'closed', closure_healthy: true }),
  LEASE({ chunk_id: 'ch_run', lease_id: 'l_run' }),
  LEASE({ chunk_id: 'ch_ask', lease_id: 'l_ask', state: 'parked', display_status: 'waiting-ask' }),
  LEASE({ chunk_id: 'ch_run2', lease_id: 'l_run2', owner: { runnerId: 'rn_2', name: 'r2', label: 'r2' } }),
]);

let stub: RequestClientStub;

afterEach(() => stub.restore());

async function render(
  inputs: Partial<{ rows: readonly MachineChunkRow[]; layout: BoardLayout; state: KitAsyncStateValue; selectedChunkId: string | null; stacked: boolean }> = {},
): Promise<{ fixture: ComponentFixture<ChunkBoard>; el: HTMLElement }> {
  stub = stubRequestClient(runnerClient, () => ({ items: [] }));
  await TestBed.configureTestingModule({
    imports: [ChunkBoard],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkBoard);
  fixture.componentRef.setInput('rows', inputs.rows ?? ROWS);
  fixture.componentRef.setInput('state', inputs.state ?? 'ready');
  if (inputs.layout) fixture.componentRef.setInput('layout', inputs.layout);
  if (inputs.selectedChunkId !== undefined) fixture.componentRef.setInput('selectedChunkId', inputs.selectedChunkId);
  if (inputs.stacked !== undefined) fixture.componentRef.setInput('stacked', inputs.stacked);
  await settle(fixture);
  return { fixture, el: fixture.nativeElement as HTMLElement };
}

const chunkIds = (root: Element | null, testid: string): (string | null)[] =>
  Array.from(root?.querySelectorAll(`[data-testid="${testid}"]`) ?? []).map((row) => row.getAttribute('data-chunk-id'));

describe('ChunkBoard', () => {
  describe('Board layout', () => {
    it('lays the chunks out in the three lanes, each headed by its label and count', async () => {
      const { el } = await render();

      expect(el.querySelector('[data-testid="board-list"]')).toBeNull();
      const lanes = Array.from(el.querySelectorAll<HTMLElement>('[data-testid^="lane-"]'));
      expect(lanes.map((lane) => lane.getAttribute('data-testid'))).toEqual(['lane-needs', 'lane-progress', 'lane-done']);
      expect(lanes.map((lane) => lane.querySelector('.col-lbl')?.textContent?.trim())).toEqual(['Needs human', 'In progress', 'Done']);
      expect(lanes.map((lane) => lane.querySelector('.n')?.textContent?.trim())).toEqual(['1', '2', '1']);
    });

    it('puts each chunk in its own lane, keeping the merged order within one', async () => {
      const { el } = await render();

      expect(chunkIds(el.querySelector('[data-testid="lane-needs"]'), 'chunk-row')).toEqual(['ch_ask']);
      expect(chunkIds(el.querySelector('[data-testid="lane-progress"]'), 'chunk-row')).toEqual(['ch_run', 'ch_run2']);
      expect(chunkIds(el.querySelector('[data-testid="lane-done"]'), 'chunk-row')).toEqual(['ch_done']);
    });

    it('names each card by the runner it was read off', async () => {
      const { el } = await render();

      const runners = Array.from(el.querySelectorAll('[data-testid="lane-progress"] [data-testid="chunk-row-runner"]'));
      expect(runners.map((runner) => runner.textContent?.trim())).toEqual(['r1', 'r2']);
    });

    it('keeps every lane, with a zero count, when one is empty', async () => {
      const { el } = await render({ rows: ROWS.filter((row) => row.lane !== 'needs') });

      expect(el.querySelector('[data-testid="lane-needs"] .n')?.textContent?.trim()).toBe('0');
      expect(chunkIds(el.querySelector('[data-testid="lane-needs"]'), 'chunk-row')).toEqual([]);
    });
  });

  describe('List layout', () => {
    it('ranks one row per chunk lane by lane — needs human, in progress, done', async () => {
      const { el } = await render({ layout: 'list' });

      expect(el.querySelector('[data-testid="board"]')).toBeNull();
      expect(chunkIds(el.querySelector('[data-testid="board-list"]'), 'list-row')).toEqual(['ch_ask', 'ch_run', 'ch_run2', 'ch_done']);
      expect(el.querySelector('[data-testid="chunk-row"]')).toBeNull();
    });

    it('heads its columns in operator words', async () => {
      const { el } = await render({ layout: 'list' });

      const cols = Array.from(el.querySelectorAll('[data-testid="board-list"] .cols span')).map((col) => col.textContent?.trim());
      expect(cols).toEqual(['Work', 'Runner · Step · Worker', 'State']);
    });
  });

  it('marks the active layout on the Board/List switch and emits the one chosen', async () => {
    const { fixture, el } = await render();
    const chosen: BoardLayout[] = [];
    fixture.componentInstance.layoutChange.subscribe((layout) => chosen.push(layout));

    expect(el.querySelector('[data-testid="board-layout-board"]')?.getAttribute('aria-selected')).toBe('true');
    el.querySelector<HTMLElement>('[data-testid="board-layout-list"]')?.click();
    el.querySelector<HTMLElement>('[data-testid="board-layout-board"]')?.click();

    expect(chosen).toEqual(['list', 'board']);
  });

  it('marks the selected chunk and re-emits a picked one, in either layout', async () => {
    for (const layout of ['board', 'list'] as const) {
      TestBed.resetTestingModule();
      stub?.restore();
      const { fixture, el } = await render({ layout, selectedChunkId: 'ch_run' });
      const picked: string[] = [];
      fixture.componentInstance.selectChunk.subscribe((id) => picked.push(id));
      const testid = layout === 'board' ? 'chunk-row' : 'list-row';

      expect(el.querySelector(`[data-testid="${testid}"].selected`)?.getAttribute('data-chunk-id')).toBe('ch_run');
      el.querySelector<HTMLElement>(`[data-testid="${testid}"][data-chunk-id="ch_done"]`)?.click();
      expect(picked).toEqual(['ch_done']);
    }
  });

  it('shows its empty text, and no lanes, when no chunk has a row', async () => {
    const { el } = await render({ rows: [], state: 'empty' });

    expect(el.querySelector('[data-testid="chunks-empty"]')?.textContent).toContain('NO CHUNKS ON THESE RUNNERS');
    expect(el.querySelector('[data-testid="board"]')).toBeNull();
  });

  it('renders no rows while loading or once the read failed', async () => {
    const loading = await render({ state: 'loading' });
    expect(loading.el.querySelector('[data-testid="board"]')).toBeNull();
    expect(loading.el.textContent).toContain('LOADING…');

    TestBed.resetTestingModule();
    stub.restore();
    const failed = await render({ state: 'error' });
    expect(failed.el.querySelector('[data-testid="board"]')).toBeNull();
    expect(failed.el.textContent).toContain('CHUNKS UNAVAILABLE');
  });

  it('stacks its lanes when asked', async () => {
    const { el } = await render({ stacked: true });

    expect(el.classList).toContain('stacked');
  });
});
