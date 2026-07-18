import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { ActivatedRoute, convertToParamMap, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { BehaviorSubject } from 'rxjs';
import { vi } from 'vitest';

import { GraphsPage } from './graphs-page';

/**
 * Exercises `GraphsPage`'s route-param plumbing (the master/detail contract phase
 * 3 requires): `/graphs` shows the list with a placeholder, `/graphs/:graphId`
 * mounts the detail beside it, refresh-safe by construction since the param
 * alone (not component state) drives which version is shown. Mounting the page
 * directly (not through a router-outlet) needs a stubbed `ActivatedRoute` whose
 * `paramMap` this test drives, so the fixture never resolves the *real* matched
 * route — `App`'s own router-outlet integration (`app.spec.ts`) is what proves
 * `/graphs/:graphId` actually resolves to this component. `GraphExplorer` and
 * `GraphDetail` own their own data-fetch behavior and are covered in their own
 * specs; the hub client's transport is stubbed to answer every hub read `404`, so
 * `GraphDetail` (and `GraphExplorer`) settle into their own error state — enough to
 * prove the id reached them. The `404` matters: since the phase-3-finding fix, the
 * graph-detail query overrides the client's `retry` with `shouldRetryGraphFetch`,
 * which retries any *non-404* failure three times with exponential backoff
 * (`graphs.query.ts`). An unstubbed jsdom fetch fails as a status-0 error, so it
 * would retry ~7s and hang `whenStable()`; a terminal 404 is the one failure that
 * settles at once.
 */
describe('GraphsPage', () => {
  let paramMap$: BehaviorSubject<ReturnType<typeof convertToParamMap>>;

  beforeEach(() => {
    hubClient.setConfig({
      baseUrl: 'http://localhost',
      fetch: (async () =>
        new Response(JSON.stringify({ detail: 'unknown graph' }), { status: 404 })) as typeof fetch,
    });
  });

  afterEach(() => {
    hubClient.setConfig({ baseUrl: '', fetch: undefined });
  });

  async function mount(graphId: string | null) {
    paramMap$ = new BehaviorSubject(convertToParamMap(graphId === null ? {} : { graphId }));
    const navigate = vi.fn();
    await TestBed.configureTestingModule({
      imports: [GraphsPage],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: ActivatedRoute, useValue: { paramMap: paramMap$ } },
        { provide: Router, useValue: { navigate } },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GraphsPage);
    await fixture.whenStable();
    return { fixture, navigate };
  }

  it('shows the placeholder and no detail when the route carries no graphId', async () => {
    const { fixture } = await mount(null);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graphs-page-placeholder"]')).toBeTruthy();
    expect(el.querySelector('fleet-graph-detail')).toBeNull();
    expect(el.querySelector('fleet-graph-explorer')).toBeTruthy();
  });

  it('mounts the detail for the graphId in the route param, keeping the list mounted too', async () => {
    const { fixture } = await mount('gr_build_v1');
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graphs-page-placeholder"]')).toBeNull();
    expect(el.querySelector('fleet-graph-detail')).toBeTruthy();
    // Master/detail, not a route swap — the list stays alongside the detail.
    expect(el.querySelector('fleet-graph-explorer')).toBeTruthy();
  });

  it('re-selects the detail when the param changes without remounting the page', async () => {
    const { fixture } = await mount('gr_build_v1');
    const pageInstance = fixture.componentInstance;

    paramMap$.next(convertToParamMap({ graphId: 'gr_review_v1' }));
    await fixture.whenStable();

    expect(fixture.componentInstance).toBe(pageInstance);
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('fleet-graph-detail')).toBeTruthy();
  });

  it('navigates to /graphs/:graphId when the explorer emits a selection', async () => {
    const { fixture, navigate } = await mount(null);

    const explorer = fixture.debugElement.query(By.css('fleet-graph-explorer'));
    explorer.componentInstance.selectGraph.emit('gr_build_v1');
    await fixture.whenStable();

    expect(navigate).toHaveBeenCalledWith(['/graphs', 'gr_build_v1']);
  });
});
