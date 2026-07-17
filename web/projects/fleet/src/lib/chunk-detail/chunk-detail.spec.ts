import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import type { ChunkDetail as ChunkDetailModel } from '../api/hub';
import { settle } from '../testing/settle';
import { type HubClientStub, stubError, stubHubClient } from '../testing/stub-hub-client';
import { ChunkDetail } from './chunk-detail';

const ROUTED_DETAIL: ChunkDetailModel = {
  chunk_id: 'ch_routed',
  graph_id: 'gr_1',
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

describe('ChunkDetail container', () => {
  let stub: HubClientStub;
  // Mutated per-test to drive the detach mutation's response (200/404/409); the stub
  // closure below reads it live, so a test can set it after the fixture is mounted.
  let detachResponse: unknown = {};

  beforeEach(async () => {
    detachResponse = {};
    // The generated client's transport is stubbed so we can assert the exact call the button fires.
    stub = stubHubClient((method, path) => {
      if (method === 'GET' && path === '/api/chunks/ch_gate') return GATE_DETAIL;
      if (method === 'GET' && path === '/api/chunks/ch_routed') return ROUTED_DETAIL;
      if (method === 'GET' && (path === '/api/chunks/ch_gate/pm-items' || path === '/api/chunks/ch_routed/pm-items')) {
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

  it('fires the resolve-decision client call when a gate choice button is clicked (D-042)', async () => {
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

  // --- Detach (D-088, issue #42) ---------------------------------------------

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
    expect(el.querySelector('[data-testid="detach-error"]')?.textContent).toContain('has no live route');
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
    expect(el.querySelector('[data-testid="detach-error"]')).not.toBeNull();

    fixture.componentRef.setInput('chunkId', 'ch_gate');
    await settle(fixture);
    el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="detach-error"]')).toBeNull();
    confirmSpy.mockRestore();
  });
});
