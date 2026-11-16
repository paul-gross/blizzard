import { Component, inject, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, type Params, Router, RouterLink, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { vi } from 'vitest';

import { APP_SCOPE, AppLink, AppPath, AppScope, type AppScopePolicy, type InAppLink } from './app-scope';

const scoped = (query: () => Params, path: (link: InAppLink) => InAppLink = (link) => link): AppScopePolicy => ({
  path,
  query,
});

const setup = (policy?: AppScopePolicy) => {
  TestBed.configureTestingModule({
    providers: [
      provideZonelessChangeDetection(),
      provideRouter([]),
      ...(policy ? [{ provide: APP_SCOPE, useValue: policy }] : []),
    ],
  });
  return { scope: TestBed.inject(AppScope), router: TestBed.inject(Router) };
};

describe('AppScope', () => {
  it('keeps every path and adds no query by default', () => {
    const { scope, router } = setup();

    expect(router.serializeUrl(scope.link(['/board', 'chunk', 'chk_1']))).toBe('/board/chunk/chk_1');
    expect(router.serializeUrl(scope.link('/board', { chunk: 'chk_1' }))).toBe('/board?chunk=chk_1');
    expect(scope.path(['/board'])).toEqual(['/board']);
  });

  it("lets a target's query key beat the scope's", () => {
    const { scope, router } = setup(scoped(() => ({ runner: 'rn_1', project: 'blizzard' })));

    expect(router.serializeUrl(scope.link('/board', { runner: 'rn_2' }))).toBe('/board?runner=rn_2&project=blizzard');
  });

  it('removes a scope key the target sets to null', () => {
    const { scope, router } = setup(scoped(() => ({ runner: 'rn_1', project: 'blizzard' })));

    expect(router.serializeUrl(scope.link('/board', { runner: null, chunk: null }))).toBe('/board?project=blizzard');
  });

  it('lets the scope fill a key the target leaves absent or undefined', () => {
    const { scope, router } = setup(scoped(() => ({ runner: 'rn_1' })));

    expect(router.serializeUrl(scope.link('/board'))).toBe('/board?runner=rn_1');
    expect(router.serializeUrl(scope.link('/board', { runner: undefined, chunk: 'c' }))).toBe('/board?runner=rn_1&chunk=c');
  });

  it("counts a string target's embedded query and fragment as the target's", () => {
    const { scope, router } = setup(scoped(() => ({ runner: 'rn_1', project: 'blizzard' })));

    expect(router.serializeUrl(scope.link('/events?runner=rn_2&kind=x#top'))).toBe(
      '/events?runner=rn_2&project=blizzard&kind=x#top',
    );
    expect(router.serializeUrl(scope.link('/events?kind=x', { kind: 'y' }))).toBe(
      '/events?runner=rn_1&project=blizzard&kind=y',
    );
  });

  it('applies the path policy to the path alone, never to the query', () => {
    const path = vi.fn((link: InAppLink) => (typeof link === 'string' ? `/p/x${link}` : link));
    const { scope, router } = setup(scoped(() => ({}), path));

    expect(router.serializeUrl(scope.link('/board?layout=list'))).toBe('/p/x/board?layout=list');
    expect(path).toHaveBeenCalledWith('/board');
  });

  it('resolves a relative target as RouterLink would', async () => {
    let route: ActivatedRoute | undefined;
    @Component({ selector: 'fleet-test-route', template: '' })
    class RouteView {
      constructor() {
        route = inject(ActivatedRoute);
      }
    }
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([{ path: 'board/chunk/:id', component: RouteView }]),
        { provide: APP_SCOPE, useValue: scoped(() => ({ runner: 'rn_1' })) },
      ],
    });
    await RouterTestingHarness.create('/board/chunk/a');
    const router = TestBed.inject(Router);
    const scope = TestBed.inject(AppScope);

    expect(router.serializeUrl(scope.link(['..', 'b'], null, route))).toBe('/board/chunk/b?runner=rn_1');
    expect(router.serializeUrl(router.createUrlTree(['..', 'b'], { relativeTo: route }))).toBe('/board/chunk/b');
  });
});

@Component({
  selector: 'fleet-test-app-link-host',
  imports: [AppLink, AppPath, RouterLink],
  template: `<a data-testid="board" [routerLink]="'/board' | appLink: { chunk: id }">Board</a>
    <a data-testid="chunk" [routerLink]="['/board', 'chunk', id] | appLink">Chunk</a>
    <a data-testid="path" [routerLink]="'/board' | appPath" queryParamsHandling="merge">Path</a>`,
})
class TestAppLinkHost {
  readonly id = 'chk_1';
}

describe('AppLink and AppPath', () => {
  it('follow the scope the app provides, as it moves', async () => {
    const runner = signal<string | null>('rn_1');
    const slug = signal<string | null>('blizzard');
    setup(
      scoped(
        () => (runner() === null ? {} : { runner: runner() }),
        (link) => (slug() === null || typeof link !== 'string' ? link : `/p/${slug()}${link}`),
      ),
    );
    const fixture = TestBed.createComponent(TestAppLinkHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const href = (id: string) => el.querySelector(`[data-testid="${id}"]`)?.getAttribute('href');

    expect(href('board')).toBe('/p/blizzard/board?runner=rn_1&chunk=chk_1');
    expect(href('chunk')).toBe('/board/chunk/chk_1?runner=rn_1');
    expect(href('path')).toBe('/p/blizzard/board');

    runner.set(null);
    slug.set(null);
    fixture.changeDetectorRef.markForCheck();
    await fixture.whenStable();

    expect(href('board')).toBe('/board?chunk=chk_1');
    expect(href('path')).toBe('/board');
  });

  it('hand back the same reference while the URL is unchanged', () => {
    setup(scoped(() => ({ runner: 'rn_1' })));
    const link = TestBed.runInInjectionContext(() => new AppLink());
    const path = TestBed.runInInjectionContext(() => new AppPath());

    const tree = link.transform(['/board', 'chunk', 'chk_1'], { tab: 'x' });
    expect(link.transform(['/board', 'chunk', 'chk_1'], { tab: 'x' })).toBe(tree);
    expect(link.transform(['/board', 'chunk', 'chk_1'], { tab: 'y' })).not.toBe(tree);

    const first = path.transform(['/board', 'chunk', 'chk_1']);
    expect(path.transform(['/board', 'chunk', 'chk_1'])).toBe(first);
    expect(path.transform(['/board', 'chunk', 'chk_2'])).not.toBe(first);
  });

  it('hand a missing link back as it is, without asking the scope', () => {
    const policy = scoped(vi.fn(() => ({})), vi.fn((link: InAppLink) => link));
    setup(policy);
    const link = TestBed.runInInjectionContext(() => new AppLink());
    const path = TestBed.runInInjectionContext(() => new AppPath());

    expect(link.transform(null)).toBeNull();
    expect(path.transform(undefined)).toBeUndefined();
    expect(policy.path).not.toHaveBeenCalled();
    expect(policy.query).not.toHaveBeenCalled();
  });
});
