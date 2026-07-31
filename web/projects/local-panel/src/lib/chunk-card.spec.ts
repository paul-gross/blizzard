import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { ChunkCard } from './chunk-card';
import type { MachineChunkStatus } from './chunk-status';

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

const STATUS: MachineChunkStatus = { label: 'RUNNING', tone: 'running' };

async function render(workItemsResponse: (method: string, path: string) => unknown) {
  const stub = stubRequestClient(runnerClient, (method, path) => {
    if (method === 'GET' && WORK_ITEMS_ROUTE.test(path)) return workItemsResponse(method, path);
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [ChunkCard],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkCard);
  fixture.componentRef.setInput('lease', LEASE());
  fixture.componentRef.setInput('status', STATUS);
  await settle(fixture);
  return { fixture, stub };
}

describe('ChunkCard', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  it('renders the compact ref, node, epoch, and status pill', async () => {
    const result = await render(() => undefined);
    stub = result.stub;
    const el = result.fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-chunk-card"]')?.textContent).toContain('C-3YJ9');
    expect(el.querySelector('[data-testid="local-chunk-card-node"]')?.textContent).toContain('build · a2');
    expect(el.querySelector('[data-testid="local-chunk-card-status"]')?.textContent).toContain('RUNNING');
  });

  it('renders on chunk_id alone when the work-items read 502s — never depends on the hub', async () => {
    const result = await render(() => stubError(502, { detail: 'stubbed route error (502)' }));
    stub = result.stub;
    const el = result.fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-chunk-card"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="local-chunk-card-title"]')?.textContent?.trim()).toBe('');
  });

  it('renders one line per work item, each clamped to two lines with its own chip and title', async () => {
    const result = await render(() => ({
      items: [
        {
          source: 'blizzard',
          ref: '61',
          label: 'blizzard#61',
          web_url: 'https://github.com/paul-gross/blizzard/issues/61',
          fetched_at: '2026-07-16T11:00:00.000Z',
          title: 'runner machine panel',
        },
        {
          source: 'blizzard',
          ref: '62',
          label: 'blizzard#62',
          web_url: 'https://github.com/paul-gross/blizzard/issues/62',
          fetched_at: '2026-07-16T11:00:00.000Z',
          title: 'mobile chunk card',
        },
      ],
    }));
    stub = result.stub;
    const el = result.fixture.nativeElement as HTMLElement;

    const title = el.querySelector('[data-testid="local-chunk-card-title"]');
    expect(title?.classList.contains('line2')).toBe(true);
    const lines = title?.querySelectorAll('.wi') ?? [];
    expect(lines).toHaveLength(2);

    const link1 = lines[0].querySelector<HTMLAnchorElement>('a.chip');
    expect(link1?.textContent).toContain('blizzard#61');
    expect(link1?.href).toBe('https://github.com/paul-gross/blizzard/issues/61');
    expect(lines[0].textContent).toContain('runner machine panel');

    const link2 = lines[1].querySelector<HTMLAnchorElement>('a.chip');
    expect(link2?.textContent).toContain('blizzard#62');
    expect(link2?.href).toBe('https://github.com/paul-gross/blizzard/issues/62');
    expect(lines[1].textContent).toContain('mobile chunk card');
  });

  it('renders a chip alone when title is missing, and a title alone when the chip is missing', async () => {
    const result = await render(() => ({
      items: [
        {
          source: 'blizzard',
          ref: '61',
          label: 'blizzard#61',
          web_url: null,
          fetched_at: '2026-07-16T11:00:00.000Z',
          title: null,
          error: 'not found',
        },
        {
          source: 'blizzard',
          ref: '62',
          label: null,
          web_url: null,
          fetched_at: '2026-07-16T11:00:00.000Z',
          title: 'mobile chunk card',
        },
      ],
    }));
    stub = result.stub;
    const el = result.fixture.nativeElement as HTMLElement;

    const title = el.querySelector('[data-testid="local-chunk-card-title"]');
    const lines = title?.querySelectorAll('.wi') ?? [];
    expect(lines).toHaveLength(2);

    expect(lines[0].querySelector('.chip')?.textContent).toContain('blizzard#61');
    expect(lines[0].textContent?.trim()).toBe('blizzard#61');

    expect(lines[1].querySelector('.chip')).toBeNull();
    expect(lines[1].textContent?.trim()).toBe('mobile chunk card');
  });

  it('emits selectChunk on click, Enter, and Space', async () => {
    const result = await render(() => undefined);
    stub = result.stub;
    const emitted: string[] = [];
    result.fixture.componentInstance.selectChunk.subscribe((id) => emitted.push(id));
    const el = result.fixture.nativeElement as HTMLElement;
    const card = el.querySelector<HTMLElement>('[data-testid="local-chunk-card"]');

    card?.click();
    card?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    card?.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));

    expect(emitted).toEqual([
      'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    ]);
  });
});
