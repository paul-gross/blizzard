import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { GardeningScopesPage } from './gardening-scopes-page';

const SCOPE = { slug: 'blizzard', description: 'the blizzard monorepo', retired: false, created_at: '2026-01-01T00:00:00Z' };

/** Stands in for `GardeningScopeDetail`, whose own behavior is
 * `gardening-scope-detail.spec.ts`'s — this spec only cares that the parent keeps
 * a detail mounted beside its list and hands the selection to the URL. */
@Component({
  selector: 'app-test-scope-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<span data-testid="scope-detail-stub"></span>',
})
class TestScopeDetail {}

@Component({
  selector: 'app-test-scopes-host',
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
class TestScopesHost {}

/** The real route table's own shape for this tab (`app.routes.ts`) — the parent
 * under test with a bare child and a `:scopeSlug` child — driven by the real
 * router rather than an `ActivatedRoute` double, since what is under test *is*
 * that the parent reads its child's param and survives the pick. */
const routes: Routes = [
  {
    path: 'gardening/scopes',
    component: GardeningScopesPage,
    children: [
      { path: '', component: TestScopeDetail },
      { path: ':scopeSlug', component: TestScopeDetail },
    ],
  },
];

/**
 * Exercises the `/gardening/scopes` list container — the scope list, its
 * selection highlight, and the navigation a row pick performs. The detail pane's
 * own content is `gardening-scope-detail.spec.ts`'s.
 */
describe('GardeningScopesPage', () => {
  let stub: RequestClientStub;

  afterEach(() => stub?.restore());

  async function render(opts: { scopes?: readonly unknown[]; url?: string } = {}) {
    const scopes = opts.scopes ?? [SCOPE];
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/scopes') return scopes;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TestScopesHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestScopesHost);
    const router = TestBed.inject(Router);
    await router.navigateByUrl(opts.url ?? '/gardening/scopes');
    await settle(fixture, 12);
    return { fixture, router, el: fixture.nativeElement as HTMLElement };
  }

  it('lists every scope by slug, marking a retired one distinctly', async () => {
    const { el } = await render({
      scopes: [SCOPE, { slug: 'stale-scope', description: 'no longer tended', retired: true, created_at: '2026-01-01T00:00:00Z' }],
    });

    const row = el.querySelector('[data-testid="gardening-scope-row-blizzard"]');
    expect(row?.textContent).toContain('blizzard');
    expect(row?.textContent).not.toContain('retired');
    expect(el.querySelector('[data-testid="gardening-scope-row-stale-scope"]')?.textContent).toContain('retired');
  });

  it('keeps a detail pane mounted on the bare route, with no row highlighted', async () => {
    const { el } = await render();

    expect(el.querySelector('[data-testid="scope-detail-stub"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-scope-row-blizzard"]')?.classList.contains('selected')).toBe(
      false,
    );
  });

  it('highlights the row the child route names', async () => {
    const { el } = await render({ url: '/gardening/scopes/blizzard' });

    expect(el.querySelector('[data-testid="gardening-scope-row-blizzard"]')?.classList.contains('selected')).toBe(
      true,
    );
  });

  it('a scopeSlug param naming an unknown scope highlights nothing, rather than a stale row', async () => {
    const { el } = await render({ url: '/gardening/scopes/ghost' });

    expect(el.querySelector('[data-testid="gardening-scope-row-blizzard"]')?.classList.contains('selected')).toBe(
      false,
    );
  });

  it('navigates to the scope route when a scope row is picked', async () => {
    const { fixture, router, el } = await render();

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-scope-row-blizzard"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/scopes/blizzard');
  });
});
