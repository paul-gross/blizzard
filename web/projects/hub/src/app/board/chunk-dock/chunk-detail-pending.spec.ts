import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { type ChunkDetail as ChunkDetailModel, hubClient } from 'fleet';
import { settle, OPERATOR_ME_RESPONSE, type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { ChunkDetail } from './chunk-detail';

const BASE: ChunkDetailModel = {
  chunk_id: 'ch_routed',
  graph_id: 'gr_1',
  status: 'running',
  status_if_paused: 'paused',
  pausable: true,
  completable: true,
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  route: { runner_id: 'rn_01', workspace_id: 'ws_01', environment_ids: [] },
};
const DETAILS: Record<string, ChunkDetailModel> = {
  ch_routed: BASE,
  ch_routed_b: { ...BASE, chunk_id: 'ch_routed_b' },
  ch_gate: { ...BASE, chunk_id: 'ch_gate', status: 'waiting_on_human', status_if_paused: 'waiting_on_human' },
};

async function confirmAction(fixture: ReturnType<typeof TestBed.createComponent<ChunkDetail>>): Promise<void> {
  await fixture.whenStable();
  (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="confirm-dialog-confirm"]')!.click();
  await fixture.whenStable();
}

async function clickMenuAction(fixture: ReturnType<typeof TestBed.createComponent<ChunkDetail>>, testid: string): Promise<void> {
  const el = fixture.nativeElement as HTMLElement;
  el.querySelector<HTMLButtonElement>('[data-testid="chunk-actions-menu"]')?.click();
  await fixture.whenStable();
  document.body.querySelector<HTMLButtonElement>(`[data-testid="${testid}"]`)?.click();
  await fixture.whenStable();
}

describe('ChunkDetail pending scope', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      const read = /^\/api\/chunks\/([^/]+)$/.exec(path);
      if (method === 'GET' && read && DETAILS[read[1]]) return DETAILS[read[1]];
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [ChunkDetail],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  // --- Per-chunk pending scope -----------------------------------------------
  //
  // The dock stays mounted across selection changes, so a pending flag is scoped to the
  // open chunk: an action held on chunk A leaves chunk B's control enabled, and A's
  // control is still disabled when A is re-selected.

  async function tick(fixture: ReturnType<typeof TestBed.createComponent<ChunkDetail>>): Promise<void> {
    for (let i = 0; i < 3; i++) {
      await new Promise((resolve) => setTimeout(resolve, 0));
      fixture.detectChanges();
    }
  }

  it('scopes a pending Pause to its own chunk across a selection change', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const queryClient = TestBed.inject(QueryClient);
    let resolveInvalidate!: () => void;
    vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(
      new Promise<void>((resolve) => (resolveInvalidate = resolve)),
    );

    el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.click();
    await confirmAction(fixture);
    await tick(fixture);
    expect(el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.disabled).toBe(true);

    fixture.componentRef.setInput('chunkId', 'ch_gate');
    await tick(fixture);
    expect(el.querySelector('[data-testid="detail-status"]')?.textContent?.trim()).toBe('waiting_on_human');
    expect(el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.disabled).toBe(false);

    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await tick(fixture);
    expect(el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.disabled).toBe(true);

    resolveInvalidate();
    await settle(fixture);
  });

  it('scopes a pending Detach to its own chunk across a selection change', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    const queryClient = TestBed.inject(QueryClient);
    let resolveInvalidate!: () => void;
    vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(
      new Promise<void>((resolve) => (resolveInvalidate = resolve)),
    );
    const detachDisabled = async (): Promise<string | null | undefined> => {
      el.querySelector<HTMLButtonElement>('[data-testid="chunk-actions-menu"]')?.click();
      await tick(fixture);
      const value = document.body.querySelector('[data-testid="detach-chunk"]')?.getAttribute('aria-disabled');
      el.querySelector<HTMLButtonElement>('[data-testid="chunk-actions-menu"]')?.click();
      await tick(fixture);
      return value;
    };
    await clickMenuAction(fixture, 'detach-chunk');
    await confirmAction(fixture);
    await tick(fixture);

    fixture.componentRef.setInput('chunkId', 'ch_routed_b');
    await tick(fixture);
    expect(await detachDisabled()).not.toBe('true');

    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await tick(fixture);
    expect(await detachDisabled()).toBe('true');

    resolveInvalidate();
    await settle(fixture);
  });
});
