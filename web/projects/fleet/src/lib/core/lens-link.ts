import { InjectionToken, Pipe, type PipeTransform, inject } from '@angular/core';

/** A router link as a template or a navigation names it: a path, or a command array. */
export type InAppLink = string | readonly unknown[];

/**
 * Rewrites an in-app link so it keeps the app's current lens — the hub board prefixes an absolute link to a view with
 * its project segment. A relative link, and any link the app does not lens, comes back unchanged. The default, for an
 * app with no lens, returns every link as it is.
 */
export const IN_APP_LINK = new InjectionToken<(link: InAppLink) => InAppLink>('fleet.IN_APP_LINK', {
  providedIn: 'root',
  factory: () => (link: InAppLink) => link,
});

/**
 * `[routerLink]="'/board' | lensLink"` — a link through {@link IN_APP_LINK}, so it keeps the lens the page is under.
 *
 * Impure, because the lens is the URL's and moves without the link changing; an unchanged answer is handed back as the
 * same value, so a binding sees a new link only when the lens actually moved it.
 */
@Pipe({ name: 'lensLink', pure: false })
export class LensLink implements PipeTransform {
  private readonly rewrite = inject(IN_APP_LINK);
  private last: InAppLink | null = null;

  transform(link: InAppLink): InAppLink {
    const next = this.rewrite(link);
    if (this.last !== null && sameLink(this.last, next)) return this.last;
    this.last = next;
    return next;
  }
}

function sameLink(a: InAppLink, b: InAppLink): boolean {
  if (typeof a === 'string' || typeof b === 'string') return a === b;
  return a.length === b.length && a.every((part, index) => part === b[index]);
}
