import { DestroyRef, Injectable, type Provider, computed, inject, signal } from '@angular/core';
import { NavigationEnd, Router } from '@angular/router';

import { HUB_PROJECT_FILTER, IN_APP_LINK, type InAppLink } from 'fleet/shell';

/** The URL segment a lensed board URL opens with: `/p/{project}/{view}/…`. */
export const LENS_SEGMENT = 'p';

/** Root segments that are never lensed: the lens's own segment and the public pages. */
const UNLENSED_ROOTS: ReadonlySet<string> = new Set([LENS_SEGMENT, 'login', 'invite']);

/** Views whose second segment is a tab of the view, kept when the lens changes. */
const TABBED_VIEWS: ReadonlySet<string> = new Set(['gardening', 'admin']);

/** The view the board opens on when a URL names none. */
const DEFAULT_VIEW = 'board';

/** The path of `url`, without its query and fragment, split into its segments. */
function pathSegments(url: string): string[] {
  const path = url.split(/[?#]/, 1)[0];
  return path.split('/').filter((segment) => segment !== '');
}

/** The project segment `url` is lensed on — the segment after `/p/` — or `null` for a URL on All. */
export function lensSegmentOf(url: string): string | null {
  const [root, segment] = pathSegments(url);
  return root === LENS_SEGMENT && segment !== undefined ? decodeURIComponent(segment) : null;
}

/** `url` on All: its lens prefix removed, its view, query and fragment kept. */
export function unlensedUrl(url: string): string {
  if (lensSegmentOf(url) === null) return url;
  const rest = url.replace(/^\/?[^/?#]+\/[^/?#]+/, '');
  return rest === '' || rest.startsWith('?') || rest.startsWith('#') ? `/${rest}` : rest;
}

/** `url`, a URL on All, under the lens of `slug` — or unchanged for `null`, or for a page the lens never reaches. */
export function lensedUrl(url: string, slug: string | null): string {
  const [root] = pathSegments(url);
  if (slug === null || (root !== undefined && UNLENSED_ROOTS.has(root))) return url;
  const prefix = `/${LENS_SEGMENT}/${encodeURIComponent(slug)}`;
  return url === '/' || url.startsWith('/?') || url.startsWith('/#') ? `${prefix}${url.slice(1)}` : `${prefix}${url}`;
}

/**
 * `link` under the lens of `slug`: an absolute link to a view gains the lens's prefix. A relative link, a link already
 * under a lens, a link to a public page, and any link while the lens is on All (`null`) come back unchanged.
 */
export function lensLink(link: string, slug: string | null): string;
export function lensLink(link: readonly unknown[], slug: string | null): readonly unknown[];
export function lensLink(link: InAppLink, slug: string | null): InAppLink;
export function lensLink(link: InAppLink, slug: string | null): InAppLink {
  if (slug === null) return link;
  if (typeof link === 'string') return link.startsWith('/') ? lensedUrl(link, slug) : link;
  const [first, ...rest] = link;
  if (typeof first !== 'string' || !first.startsWith('/')) return link;
  const segments = first.split('/').filter((segment) => segment !== '');
  if (segments.length > 0 && UNLENSED_ROOTS.has(segments[0])) return link;
  return [`/${LENS_SEGMENT}`, slug, ...segments, ...rest];
}

/** The view part of `url` that a lens change keeps: the view, and the tab of a tabbed view. A record, a query and a
 * fragment belong to the project being left, so they are dropped. */
export function viewOf(url: string): string {
  const segments = pathSegments(unlensedUrl(url));
  const view = segments[0] ?? DEFAULT_VIEW;
  const kept = TABBED_VIEWS.has(view) && segments[1] !== undefined ? [view, segments[1]] : [view];
  return `/${kept.join('/')}`;
}

/**
 * The project lens — the project the board's views are narrowed to, read from the URL and held nowhere else, so a
 * refresh or a shared link reproduces it: `/{view}/…` is the lens on All, `/p/{project}/{view}/…` the lens on one
 * project, named by its current slug once the lens guard has settled it.
 *
 * It is shell state inside the tenant: the board's tenant is settled above the shell first, and a lens is resolved only
 * once {@link settle} reports the shell entered it ({@link whenReady}). A tenant switch reloads the page, so a lens
 * never outlives its tenant.
 *
 * {@link link} keeps a link under the lens; {@link switchTo} re-navigates the view the board is on under another lens.
 */
@Injectable({ providedIn: 'root' })
export class ProjectLens {
  private readonly router = inject(Router);

  private readonly url = signal(this.router.url);
  private readonly shellReady = signal(false);
  private readonly noticeText = signal<string | null>(null);
  private waiting: (() => void)[] = [];

  /** The slug of the project the lens is on, `null` for All. */
  readonly slug = computed(() => lensSegmentOf(this.url()));

  /** Whether the lens is on one project. */
  readonly lensed = computed(() => this.slug() !== null);

  /** A notice for the person — a link that named no project of the tenant, opened on All — `null` for none. */
  readonly notice = this.noticeText.asReadonly();

  /** Whether the shell has entered its tenant, so a lens may be resolved in it. */
  readonly ready = this.shellReady.asReadonly();

  constructor() {
    const subscription = this.router.events.subscribe((event) => {
      if (event instanceof NavigationEnd) this.url.set(event.urlAfterRedirects);
    });
    inject(DestroyRef).onDestroy(() => subscription.unsubscribe());
  }

  /** `link` under the lens the board is on — see {@link lensLink}. */
  link(link: string): string;
  link(link: readonly unknown[]): readonly unknown[];
  link(link: InAppLink): InAppLink;
  link(link: InAppLink): InAppLink {
    return lensLink(link, this.slug());
  }

  /** The URL of the view the board is on, under the lens of `slug` (`null` for All) — see {@link viewOf}. */
  viewUnder(slug: string | null): string {
    return lensedUrl(viewOf(this.url()), slug);
  }

  /** Puts the lens on `slug` (`null` for All), keeping the view the board is on. */
  switchTo(slug: string | null): Promise<boolean> {
    this.noticeText.set(null);
    return this.router.navigateByUrl(this.viewUnder(slug));
  }

  /** Shows `text` as the lens's notice until it is dismissed or the lens is switched. */
  notify(text: string): void {
    this.noticeText.set(text);
  }

  /** Dismisses the lens's notice. */
  dismissNotice(): void {
    this.noticeText.set(null);
  }

  /** The shell reports whether it has entered its tenant; the first `true` releases every {@link whenReady}. */
  settle(ready: boolean): void {
    this.shellReady.set(ready);
    if (!ready) return;
    const waiting = this.waiting;
    this.waiting = [];
    for (const release of waiting) release();
  }

  /** Resolves once the shell has entered its tenant — at once if it already has. */
  whenReady(): Promise<void> {
    if (this.shellReady()) return Promise.resolve();
    return new Promise((release) => this.waiting.push(release));
  }
}

/** The lens's providers for the hub app: the reads' project filter and the links' rewrite, both from {@link ProjectLens}. */
export function provideProjectLens(): Provider[] {
  return [
    { provide: HUB_PROJECT_FILTER, useFactory: () => inject(ProjectLens).slug },
    {
      provide: IN_APP_LINK,
      useFactory: () => {
        const lens = inject(ProjectLens);
        return (link: InAppLink) => lens.link(link);
      },
    },
  ];
}
