import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { routes } from '../app.routes';

/**
 * The `/gardening` subtree in the real route table (`app.routes.ts`, blizzard#397) —
 * proves the three children resolve and the bare parent path redirects, through the
 * actual router rather than by mounting a page component directly (that's each
 * page's own spec). A bare `<router-outlet>` host stands in for `App`'s heavier one
 * (auth session gate, live-update spine) — irrelevant to whether this route subtree
 * itself is wired correctly.
 */
@Component({
  selector: 'app-test-gardening-route-host',
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
class TestGardeningRouteHost {}

describe('the /gardening route subtree', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/garden-proposals') return [];
      if (method === 'GET' && path === '/api/routines') return [];
      if (method === 'GET' && path === '/api/graphs') return [];
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TestGardeningRouteHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
      ],
    }).compileComponents();
  });

  afterEach(() => stub.restore());

  it('redirects the bare /gardening path to /gardening/routines', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening');
    await settle(fixture);

    expect(router.url).toBe('/gardening/routines');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-routines-empty"]')).toBeTruthy();
  });

  it('resolves /gardening/runs-and-findings to its own sub-tab', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening/runs-and-findings');
    await settle(fixture);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-runs-findings-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-tab-runs-and-findings"].active')).toBeTruthy();
  });

  it('resolves /gardening/proposals to its own sub-tab, deep-linkable on its own', async () => {
    const fixture = TestBed.createComponent(TestGardeningRouteHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/gardening/proposals');
    await settle(fixture);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-proposals-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-tab-proposals"].active')).toBeTruthy();
  });
});
