import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { NavigationEnd, Router } from '@angular/router';

/** The query param naming the runner the panel is narrowed to, by its name on the host. */
export const RUNNER_PARAM = 'runner';

/** The query param naming the project the panel is narrowed to, by its slug. */
export const PROJECT_PARAM = 'project';

/**
 * The panel's two filters — the runner and the project every page is narrowed to — read
 * from the URL's `?runner=` and `?project=` and held nowhere else, so a reload or a shared
 * link reproduces them. Each is single-select; absent is All.
 *
 * The one reader of those params: pages, reads, and nav controls ask {@link runner} and
 * {@link project}, never the route. A switch rewrites only its own param, keeping the page
 * and every other param — the board's `?chunk=` included.
 */
@Injectable({ providedIn: 'root' })
export class PanelFilters {
  private readonly router = inject(Router);

  private readonly url = signal(this.router.url);
  private readonly params = computed(() => this.router.parseUrl(this.url()).queryParamMap);

  /** The name of the runner the panel is narrowed to, `null` for All. */
  readonly runner = computed(() => this.params().get(RUNNER_PARAM));

  /** The slug of the project the panel is narrowed to, `null` for All. */
  readonly project = computed(() => this.params().get(PROJECT_PARAM));

  constructor() {
    const subscription = this.router.events.subscribe((event) => {
      if (event instanceof NavigationEnd) this.url.set(event.urlAfterRedirects);
    });
    inject(DestroyRef).onDestroy(() => subscription.unsubscribe());
  }

  /** Narrows the panel to the runner named `name` (`null` for All), staying on the page. */
  setRunner(name: string | null): Promise<boolean> {
    return this.set(RUNNER_PARAM, name, this.runner());
  }

  /** Narrows the panel to the project `slug` (`null` for All), staying on the page. */
  setProject(slug: string | null): Promise<boolean> {
    return this.set(PROJECT_PARAM, slug, this.project());
  }

  private set(param: string, value: string | null, current: string | null): Promise<boolean> {
    if (value === current) return Promise.resolve(false);
    return this.router.navigate([], { queryParams: { [param]: value }, queryParamsHandling: 'merge' });
  }
}
