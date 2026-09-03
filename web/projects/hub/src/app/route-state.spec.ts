import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, type Signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter, Router, RouterOutlet } from '@angular/router';
import { settle } from 'fleet/testing';

import { injectChildRouteParam, injectQueryFilters, type QueryFilters } from './route-state';

/**
 * Pins the two router reads every gardening tab's list route is built on
 * (`route-state.ts`) against the real router, since both trade on behavior no
 * type signature states: that a parent survives navigation between its own two
 * children, that it can see the active child's param, and that a query-param
 * patch leaves the path — the detail child's segment included — untouched.
 */
@Component({
  selector: 'app-test-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<span data-testid="detail">detail</span>',
})
class TestDetail {}

/** Counts its own constructions, so a spec can prove the parent was never rebuilt. */
let parentConstructions = 0;

@Component({
  selector: 'app-test-parent',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet],
  template: '<span data-testid="picked">{{ picked() ?? "none" }}</span><router-outlet />',
})
class TestParent {
  readonly picked: Signal<string | null> = injectChildRouteParam('itemId');
  readonly filters: QueryFilters = injectQueryFilters();
  constructor() {
    parentConstructions += 1;
  }
}

@Component({
  selector: 'app-test-route-state-host',
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
class TestHost {}

const routes = [
  {
    path: 'things',
    component: TestParent,
    children: [
      { path: '', component: TestDetail },
      { path: ':itemId', component: TestDetail },
    ],
  },
];

describe('route-state', () => {
  beforeEach(async () => {
    parentConstructions = 0;
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection(), provideRouter(routes)],
    }).compileComponents();
  });

  function picked(fixture: { nativeElement: unknown }): string | undefined {
    return ((fixture.nativeElement as HTMLElement).querySelector('[data-testid="picked"]') ?? undefined)?.textContent
      ?.trim();
  }

  it('reads the active child param, and keeps the parent mounted across the pair', async () => {
    const fixture = TestBed.createComponent(TestHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/things');
    await settle(fixture);
    expect(picked(fixture)).toBe('none');
    expect(parentConstructions).toBe(1);

    await router.navigateByUrl('/things/abc');
    await settle(fixture);
    expect(picked(fixture)).toBe('abc');

    await router.navigateByUrl('/things/def');
    await settle(fixture);
    expect(picked(fixture)).toBe('def');

    await router.navigateByUrl('/things');
    await settle(fixture);
    expect(picked(fixture)).toBe('none');

    // The whole point of the nesting: one construction across all four.
    expect(parentConstructions).toBe(1);
  });

  it('patches query params without disturbing the path, and drops a null', async () => {
    const fixture = TestBed.createComponent(TestHost);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/things/abc?state=live');
    await settle(fixture);
    // The parent is mounted by the router, so it is reached through the fixture's
    // own debug tree rather than off a fixture of its own.
    const parent = fixture.debugElement.query(By.directive(TestParent)).componentInstance as TestParent;
    expect(parent.filters.read('state')).toBe('live');
    expect(parent.filters.read('class')).toBeNull();

    parent.filters.patch({ class: 'design' });
    await settle(fixture);
    expect(router.url).toBe('/things/abc?state=live&class=design');
    expect(parent.filters.read('class')).toBe('design');
    expect(picked(fixture)).toBe('abc');

    parent.filters.patch({ state: null });
    await settle(fixture);
    expect(router.url).toBe('/things/abc?class=design');
    expect(parent.filters.read('state')).toBeNull();

    // Still the same parent: a filter change never rebuilt it.
    expect(parentConstructions).toBe(1);
  });
});
