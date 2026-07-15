import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import type { ChunkDetail as ChunkDetailModel } from '../api/hub';
import { settle } from '../testing/settle';
import { type HubClientStub, stubHubClient } from '../testing/stub-hub-client';
import { ChunkDetail } from './chunk-detail';

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

  beforeEach(async () => {
    // The generated client's transport is stubbed so we can assert the exact call the button fires.
    stub = stubHubClient((method, path) => {
      if (method === 'GET' && path === '/api/chunks/ch_gate') return GATE_DETAIL;
      if (method === 'GET' && path === '/api/chunks/ch_gate/pm-items') {
        return {
          items: [
            {
              provider: 'github',
              url: 'https://github.com/acme/widget/issues/42',
              label: 'gh:widget#42',
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

  it('fetches the chunk’s PM items through the generated client and renders them on the Issue tab (issue #24)', async () => {
    const fixture = TestBed.createComponent(ChunkDetail);
    fixture.componentRef.setInput('chunkId', 'ch_gate');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="tab-issue"]')?.click();
    await settle(fixture);

    // It went through the real pass-through route (bzh:generated-client), no hand-written fetch.
    expect(stub.forRoute('/api/chunks/ch_gate/pm-items', 'GET')).toHaveLength(1);
    expect(el.querySelector('[data-testid="issue-body"]')?.textContent).toContain('reproduces under load');
    expect(el.querySelector('[data-testid="issue-message"]')?.textContent).toContain('seen it too');
  });
});
