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
      executor: 'runner',
      session: 'fresh',
      judged_by: 'worker',
      choices: [{ choice_id: 'c_pass', name: 'pass', description: 'Build succeeded' }],
    },
    {
      node_id: 'n_review',
      name: 'review',
      executor: 'runner',
      session: 'fresh',
      judged_by: 'worker',
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
    const fixture = await mount('gr_build_v2', (method, path) => {
      if (method === 'GET' && path === '/api/graphs/gr_build_v2') return GRAPH;
      return {};
    });
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/graphs/gr_build_v2/retire', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toMatchObject({ by: 'operator' });
  });

  it('fires the enable client call for a retired graph once the header emits enable (operator confirmed)', async () => {
    const fixture = await mount('gr_build_v2', (method, path) => {
      if (method === 'GET' && path === '/api/graphs/gr_build_v2') return { ...GRAPH, enabled: false, retired: true };
      return {};
    });
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-enable"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/graphs/gr_build_v2/enable', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toMatchObject({ by: 'operator' });
  });

  it('surfaces a 409 refusal from retire rather than swallowing it', async () => {
    const fixture = await mount('gr_build_v2', (method, path) => {
      if (method === 'GET' && path === '/api/graphs/gr_build_v2') return GRAPH;
      if (method === 'POST' && path === '/api/graphs/gr_build_v2/retire') {
        return stubError(409, { detail: 'graph gr_build_v2 already retired somehow' });
      }
      return {};
    });
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="graph-detail-lifecycle-error"]')?.textContent).toContain(
      'already retired somehow',
    );
  });

  // --- Pending lifecycle override (`bzh:frontend-pending-override`) ----------------
  //
  // `enabled`/`retired` is a plain two-valued fact this exact mutation sets directly
  // (`blizzard-context:/domain/graphs/identity.md`) — both directions are total, unlike
  // chunk detail's Resume/Detach. Every "held pending" assertion below spies on
  // `queryClient.invalidateQueries` and returns a promise it controls rather than
  // letting the stub's fetch settle on its own — a mutation stays `isPending()` true
  // only until its own invalidations resolve, so holding that promise open is what
  // keeps the window a real assertion can land in (`chunk-detail.spec.ts`'s own idiom).

  it('renders the retired override while a retire is pending, reverting to enabled on rejection', async () => {
    const fixture = await mount('gr_build_v2', (method, path) => {
      if (method === 'GET' && path === '/api/graphs/gr_build_v2') return GRAPH;
      if (method === 'POST' && path === '/api/graphs/gr_build_v2/retire') {
        return stubError(409, { detail: 'graph gr_build_v2 already retired somehow' });
      }
      return {};
    });
    const el = fixture.nativeElement as HTMLElement;
    const queryClient = TestBed.inject(QueryClient);
    let resolveInvalidate!: () => void;
    vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(
      new Promise<void>((resolve) => (resolveInvalidate = resolve)),
    );

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    // Held open by the `invalidateQueries` spy above — `settle()`'s own `whenStable()`
    // would hang on it, so a bare macrotask tick + a manual `detectChanges()` stands in.
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();

    expect(el.querySelector('[data-testid="graph-detail-lifecycle-badge"]')?.textContent).toContain('retired');

    resolveInvalidate();
    await settle(fixture);

    expect(el.querySelector('[data-testid="graph-detail-lifecycle-badge"]')?.textContent).toContain('enabled');
    expect(el.querySelector('[data-testid="graph-detail-lifecycle-error"]')?.textContent).toContain(
      'already retired somehow',
    );
  });

  it('renders the enabled override while an enable is pending, reverting to retired on rejection', async () => {
    const fixture = await mount('gr_build_v2', (method, path) => {
      if (method === 'GET' && path === '/api/graphs/gr_build_v2') return { ...GRAPH, enabled: false, retired: true };
      if (method === 'POST' && path === '/api/graphs/gr_build_v2/enable') {
        return stubError(409, { detail: 'graph gr_build_v2 already enabled somehow' });
      }
      return {};
    });
    const el = fixture.nativeElement as HTMLElement;
    const queryClient = TestBed.inject(QueryClient);
    let resolveInvalidate!: () => void;
    vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(
      new Promise<void>((resolve) => (resolveInvalidate = resolve)),
    );

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-enable"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();

    expect(el.querySelector('[data-testid="graph-detail-lifecycle-badge"]')?.textContent).toContain('enabled');

    resolveInvalidate();
    await settle(fixture);

    expect(el.querySelector('[data-testid="graph-detail-lifecycle-badge"]')?.textContent).toContain('retired');
    expect(el.querySelector('[data-testid="graph-detail-lifecycle-error"]')?.textContent).toContain(
      'already enabled somehow',
    );
  });

  it("scopes the override to the pending graph — navigating to a different graph mid-flight shows that graph's own real badge, not the stale prediction", async () => {
    const fixture = await mount('gr_build_v2', (method, path) => {
      if (method === 'GET' && path === '/api/graphs/gr_build_v2') return GRAPH;
      if (method === 'GET' && path === '/api/graphs/gr_other') return { ...GRAPH, graph_id: 'gr_other', retired: false };
      if (method === 'POST' && path === '/api/graphs/gr_build_v2/retire') return {};
      return {};
    });
    const el = fixture.nativeElement as HTMLElement;
    const queryClient = TestBed.inject(QueryClient);
    // Never resolved — this test only cares that a *different* graph's badge stays
    // unaffected while this one is held pending, not that it eventually settles.
    vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(new Promise<void>(() => undefined));

    el.querySelector<HTMLButtonElement>('[data-testid="graph-detail-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();

    expect(el.querySelector('[data-testid="graph-detail-lifecycle-badge"]')?.textContent).toContain('retired');

    // The retire mutation above is still held pending — a `settle()` (or bare
    // `whenStable()`) would hang on its own never-resolving `invalidateQueries`, so a
    // macrotask tick + a manual `detectChanges()` stands in, the same idiom the tests
    // above use for the same reason.
    fixture.componentRef.setInput('graphId', 'gr_other');
    for (let i = 0; i < 4; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 0));
      fixture.detectChanges();
    }

    expect(el.querySelector('[data-testid="graph-detail-graph-id"]')?.textContent).toContain('gr_other');
    expect(el.querySelector('[data-testid="graph-detail-lifecycle-badge"]')?.textContent).toContain('enabled');
  });
});
