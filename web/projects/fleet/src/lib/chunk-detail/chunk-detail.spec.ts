import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import type { ChunkDetail as ChunkDetailModel } from '../api/hub';
import { settle } from '../testing/settle';
import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { ChunkDetail } from './chunk-detail';

const ROUTED_DETAIL: ChunkDetailModel = {
  chunk_id: 'ch_routed',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  pm_pointers: [],
  history: [],
  artifacts: [],
  route: { runner_id: 'rn_01', workspace_id: 'ws_01', environment_ids: [] },
};

const GATE_DETAIL: ChunkDetailModel = {
  chunk_id: 'ch_gate',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'waiting_on_human',
  current_node_id: 'nd_gate',
  latest_epoch: 1,
  pm_pointers: [],
  history: [],
  artifacts: [],
  decision: {
    decision_id: 'de_42',
    chunk_id: 'ch_gate',
    node_id: 'nd_gate',
    node_name: 'approve-gate',
    epoch: 1,
    submitted_at: '2026-07-13T00:00:01Z',
    choices: [
      { name: 'approve', description: 'Ship it.' },
      { name: 'reject', description: 'Send it back.' },
    ],
    transitioned: false,
  },
};

// A chunk carrying an open pause fact while its derived status reads waiting_on_human —
// the overlap PAUSED's position below the human-gated states creates (issue #46).
const PAUSED_ASKING_DETAIL: ChunkDetailModel = {
  ...GATE_DETAIL,
  chunk_id: 'ch_paused',
  pause: { by: 'operator', set_at: '2026-07-16T00:00:00Z' },
  decision: undefined,
};

// A not_ready chunk — the one window issue #27's graph/model edit is open.
const NOT_READY_DETAIL: ChunkDetailModel = {
  chunk_id: 'ch_ready',
  graph_id: 'gr_default',
  model: 'claude-opus-4-8',
  status: 'not_ready',
  current_node_id: null,
  latest_epoch: null,
  pm_pointers: [],
  history: [],
  artifacts: [],
};

describe('ChunkDetail container', () => {
  let stub: RequestClientStub;
  // Mutated per-test to drive the detach mutation's response (200/404/409); the stub
  // closure below reads it live, so a test can set it after the fixture is mounted.
  let detachResponse: unknown = {};
  // The same, for the pause/resume verbs (issue #46).
  let pauseResponse: unknown = {};
  // The same, for the graph/model edits (issue #27).
  let editGraphResponse: unknown = {};
  let editModelResponse: unknown = {};

  beforeEach(async () => {
    detachResponse = {};
    pauseResponse = {};
    editGraphResponse = {};
    editModelResponse = {};
    // The generated client's transport is stubbed so we can assert the exact call the button fires.
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/chunks/ch_gate') return GATE_DETAIL;
      if (method === 'GET' && path === '/api/chunks/ch_routed') return ROUTED_DETAIL;
      if (method === 'GET' && path === '/api/chunks/ch_paused') return PAUSED_ASKING_DETAIL;
      if (method === 'GET' && path === '/api/chunks/ch_ready') return NOT_READY_DETAIL;
      if (method === 'POST' && (path === '/api/chunks/ch_routed/pause' || path === '/api/chunks/ch_paused/resume')) {
        return pauseResponse;
      }
      if (method === 'POST' && path === '/api/chunks/ch_ready/graph') return editGraphResponse;
      if (method === 'POST' && path === '/api/chunks/ch_ready/model') return editModelResponse;
      if (method === 'GET' && path.endsWith('/pm-items')) {
        return {
          items: [
            {
              source: 'widget',
              ref: '42',
              label: 'widget#42',
              web_url: 'https://github.com/acme/widget/issues/42',
              fetched_at: '2026-07-15T00:00:00Z',
              body: 'the widget flake reproduces under load',
              comments: ['seen it too'],
              error: null,
            },
          ],
        };
      }
      if (path === '/api/decisions/de_42/resolution') {
        return { decision_id: 'de_42', choice: 'approve', resolved_at: 'x', resolved_by: 'operator' };
      }
      if (method === 'POST' && path === '/api/chunks/ch_routed/detach') return detachResponse;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [ChunkDetail],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('holds an empty rest state — not the detail panel — while no chunk is selected (issue #21)', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    // chunkId defaults to null: the dock stays mounted but empty.
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('fleet-chunk-detail-panel')).toBeNull();
    const rest = el.querySelector('[data-testid="chunk-detail-empty"]');
    expect(rest?.textContent).toContain('SELECT');
  });

  it('fires the resolve-decision client call when a gate choice button is clicked', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_gate');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const buttons = [...el.querySelectorAll<HTMLButtonElement>('[data-testid="decision-choice"]')];
    expect(buttons.map((b) => b.textContent?.trim())).toEqual(['approve', 'reject']);

    buttons[0].click();
    await settle(fixture);

    const calls = stub.forRoute('/api/decisions/de_42/resolution', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toMatchObject({ choice: 'approve' });
  });

  it('fetches the chunk’s PM items through the generated client and renders them in the work-item column (issue #24)', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_gate');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    // It went through the real pass-through route (bzh:generated-client), no hand-written fetch.
    expect(stub.forRoute('/api/chunks/ch_gate/pm-items', 'GET')).toHaveLength(1);
    expect(el.querySelector('[data-testid="issue-body"]')?.textContent).toContain('reproduces under load');
    expect(el.querySelector('[data-testid="issue-message"]')?.textContent).toContain('seen it too');
  });

  // --- Detach (issue #42) ---------------------------------------------

  it('fires the detach client call for a routed chunk once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();
    await settle(fixture);

    expect(stub.forRoute('/api/chunks/ch_routed/detach', 'POST')).toHaveLength(1);
    confirmSpy.mockRestore();
  });

  it('surfaces the 409 "no live route" response rather than swallowing it', async () => {
    detachResponse = stubError(409, { detail: 'chunk ch_routed has no live route' });
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();
    await settle(fixture);

    expect(stub.forRoute('/api/chunks/ch_routed/detach', 'POST')).toHaveLength(1);
    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('has no live route');
    confirmSpy.mockRestore();
  });

  it('clears a stale detach error when a different chunk is opened', async () => {
    detachResponse = stubError(409, { detail: 'chunk ch_routed has no live route' });
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    let el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();
    await settle(fixture);
    expect(el.querySelector('[data-testid="action-error"]')).not.toBeNull();

    fixture.componentRef.setInput('chunkId', 'ch_gate');
    await settle(fixture);
    el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="action-error"]')).toBeNull();
    confirmSpy.mockRestore();
  });

  // --- Pause / Resume (issue #46) --------------------------------------------

  it('fires the pause client call for a running chunk once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/chunks/ch_routed/pause', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toMatchObject({ by: 'operator' });
    confirmSpy.mockRestore();
  });

  it('fires the resume client call for a paused chunk whose status reads waiting_on_human (issue #46)', async () => {
    // The overlap, end to end through the generated client: the dock reads the pause
    // fact off ChunkDetail, so it offers Resume for a chunk whose status hides the pause.
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_paused');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detail-status"]')?.textContent).toContain('waiting_on_human');
    expect(el.querySelector('[data-testid="pause-chunk"]')).toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="resume-chunk"]')?.click();
    await settle(fixture);

    expect(stub.forRoute('/api/chunks/ch_paused/resume', 'POST')).toHaveLength(1);
    confirmSpy.mockRestore();
  });

  it('surfaces a 409 refusal from pause in the shared notice rather than swallowing it', async () => {
    pauseResponse = stubError(409, { detail: 'chunk ch_routed is not pausable (delivering)' });
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('not pausable');
    confirmSpy.mockRestore();
  });

  // --- Graph / model edit (issue #27) -----------------------------------------

  it('fires the graph edit client call for a not_ready chunk', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ready');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="graph-input"]')!;
    input.value = 'gr_alt';
    el.querySelector<HTMLButtonElement>('[data-testid="graph-submit"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/chunks/ch_ready/graph', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ graph_id: 'gr_alt' });
  });

  it('fires the model edit client call for a not_ready chunk', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ready');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="model-input"]')!;
    input.value = 'claude-sonnet-4-5';
    el.querySelector<HTMLButtonElement>('[data-testid="model-submit"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/chunks/ch_ready/model', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ model: 'claude-sonnet-4-5' });
  });

  it('offers no graph/model edit inputs for a chunk that has left not_ready', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_routed');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-input"]')).toBeNull();
    expect(el.querySelector('[data-testid="model-input"]')).toBeNull();
  });

  it('surfaces a 409 refusal from the graph edit rather than swallowing it', async () => {
    editGraphResponse = stubError(409, { detail: 'chunk ch_ready is already ready' });
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ready');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="graph-input"]')!;
    input.value = 'gr_alt';
    el.querySelector<HTMLButtonElement>('[data-testid="graph-submit"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('already ready');
  });

  it('surfaces a 422 refusal from the model edit rather than swallowing it', async () => {
    editModelResponse = stubError(422, { detail: 'model must not be blank' });
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_ready');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="model-input"]')!;
    input.value = 'x';
    el.querySelector<HTMLButtonElement>('[data-testid="model-submit"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('must not be blank');
  });
});
