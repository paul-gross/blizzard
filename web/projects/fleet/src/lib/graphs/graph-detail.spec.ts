import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { settle } from '../testing/settle';
import { client as hubClient } from '../api/hub/client.gen';
import { OPERATOR_ME_RESPONSE } from '../testing/auth-fixtures';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { GraphDetail } from './graph-detail';

const GRAPH = {
  graph_id: 'gr_build_v2',
  name: 'build',
  enabled: true,
  entry_node_id: 'n_build',
  nodes: [
    {
      node_id: 'n_build',
      name: 'build',
      executor: 'claude',
      session: 'fresh',
      judged_by: 'reviewer',
      choices: [{ choice_id: 'c_pass', name: 'pass', description: 'Build succeeded' }],
    },
    {
      node_id: 'n_review',
      name: 'review',
      executor: 'claude',
      session: 'fresh',
      judged_by: 'reviewer',
      choices: [],
    },
  ],
  edges: [
    { from_node_id: 'n_build', choice_id: 'c_pass', to_node_name: 'review', prompt_addendum: 'Focus on tests.' },
  ],
  warnings: [],
};

describe('GraphDetail', () => {
  let stub: RequestClientStub;

  async function mount(graphId: string, route: (m: string, p: string) => unknown, me: unknown = OPERATOR_ME_RESPONSE) {
    // Every mount resolves `/api/me` (the graph-edit gate reads it); the per-test
    // `route` handles the graph reads. `me` defaults to the full-permission operator so
    // the lifecycle controls render, exactly as before the #93 gating landed.
    stub = stubRequestClient(hubClient, (m, p) => {
      if (m === 'GET' && p === '/api/me') return me;
      return route(m, p);
    });
    await TestBed.configureTestingModule({
      imports: [GraphDetail],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GraphDetail);
    fixture.componentRef.setInput('graphId', graphId);
    await settle(fixture);
    return fixture;
  }

  afterEach(() => stub?.restore());

  it('shows an error state for an unknown graph id', async () => {
    const fixture = await mount('gr_missing', () => stubError(404, { detail: 'unknown graph' }));
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-detail-error"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-detail-body"]')).toBeNull();
  });

  // --- Retire / re-enable mutation wiring (issue #101) -----------------------------

  it('fires the retire client call once the header emits retire (operator confirmed)', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = await mount('gr_build_v2', (method, path) => {
      if (method === 'GET' && path === '/api/graphs/gr_build_v2') return GRAPH;
      return {};
    });
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-retire"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/graphs/gr_build_v2/retire', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toMatchObject({ by: 'operator' });
    confirmSpy.mockRestore();
  });

  it('fires the enable client call for a retired graph once the header emits enable (operator confirmed)', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = await mount('gr_build_v2', (method, path) => {
      if (method === 'GET' && path === '/api/graphs/gr_build_v2') return { ...GRAPH, enabled: false, retired: true };
      return {};
    });
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-enable"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/graphs/gr_build_v2/enable', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toMatchObject({ by: 'operator' });
    confirmSpy.mockRestore();
  });

  it('surfaces a 409 refusal from retire rather than swallowing it', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = await mount('gr_build_v2', (method, path) => {
      if (method === 'GET' && path === '/api/graphs/gr_build_v2') return GRAPH;
      if (method === 'POST' && path === '/api/graphs/gr_build_v2/retire') {
        return stubError(409, { detail: 'graph gr_build_v2 already retired somehow' });
      }
      return {};
    });
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-retire"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="graph-detail-lifecycle-error"]')?.textContent).toContain(
      'already retired somehow',
    );
    confirmSpy.mockRestore();
  });
});
