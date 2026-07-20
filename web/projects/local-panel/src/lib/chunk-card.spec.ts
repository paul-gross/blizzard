import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { ChunkCard } from './chunk-card';
import type { MachineChunkStatus } from './chunk-status';

/** Matches `GET /api/chunks/{chunk_id}/pm-items` for any chunk id. */
const PM_ITEMS_ROUTE = /^\/api\/chunks\/[^/]+\/pm-items$/;

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

async function render(pmItemsResponse: (method: string, path: string) => unknown) {
  const stub = stubRequestClient(runnerClient, (method, path) => {
    if (method === 'GET' && PM_ITEMS_ROUTE.test(path)) return pmItemsResponse(method, path);
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

  it('renders on chunk_id alone when the pm-items read 502s — never depends on the hub', async () => {
    const result = await render(() => stubError(502, { detail: 'stubbed route error (502)' }));
    stub = result.stub;
    const el = result.fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-chunk-card"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="local-chunk-card-title"]')?.textContent?.trim()).toBe('');
  });

  it('renders the pm-item chip inline with the title, wrapped to two lines', async () => {
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
      ],
    }));
    stub = result.stub;
    const el = result.fixture.nativeElement as HTMLElement;

    const title = el.querySelector('[data-testid="local-chunk-card-title"]');
    expect(title?.classList.contains('line2')).toBe(true);
    const link = title?.querySelector<HTMLAnchorElement>('a.chip');
    expect(link?.textContent).toContain('blizzard#61');
    expect(link?.href).toBe('https://github.com/paul-gross/blizzard/issues/61');
    expect(title?.textContent).toContain('runner machine panel');
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
