import { Injectable, InjectionToken, Pipe, type PipeTransform, inject } from '@angular/core';
import { ActivatedRoute, type Params, Router, type UrlTree } from '@angular/router';

/** A router link as a template or a navigation names it: a path, or a command array. */
export type InAppLink = string | readonly unknown[];

/**
 * A link's own query: a key set to a value names it, a key set to `null` removes it even when the app's scope supplies
 * it, and an absent or `undefined` key lets the scope fill it.
 */
export type TargetQuery = Readonly<Record<string, unknown>>;

/**
 * What an app supplies to keep its scope on its links — policy only; how the two compose is {@link AppScope}'s.
 * `path` rewrites a link's path (the hub prefixes its project lens); `query` reads the scope's query, whose keys the
 * app owns. Either may read signals, so a link follows the scope as it moves.
 */
export interface AppScopePolicy {
  readonly path: (link: InAppLink) => InAppLink;
  readonly query: () => Params;
}

/** The app's scope policy. The default, for an app with no scope, keeps every path and adds no query. */
export const APP_SCOPE = new InjectionToken<AppScopePolicy>('fleet.APP_SCOPE', {
  providedIn: 'root',
  factory: () => ({ path: (link: InAppLink) => link, query: () => ({}) }),
});

/**
 * A link that keeps the app's scope. {@link link} is a cross-page link or navigation: the target's path and query
 * composed with the scope into one `UrlTree`. {@link path} is path policy only, for an anchor that keeps the current
 * query itself through `queryParamsHandling`.
 */
@Injectable({ providedIn: 'root' })
export class AppScope {
  private readonly router = inject(Router);
  private readonly policy = inject(APP_SCOPE);

  /** `link` with the app's path policy applied — a relative link, and any link the app does not rewrite, unchanged. */
  path(link: InAppLink): InAppLink;
  path(link: InAppLink | null | undefined): InAppLink | null | undefined;
  path(link: InAppLink | null | undefined): InAppLink | null | undefined {
    return link === null || link === undefined ? link : this.policy.path(link);
  }

  /**
   * `target` under the app's scope, as a `UrlTree`. The target's query — a string target's embedded `?query` and
   * `#fragment`, then `query` — beats the scope's (see {@link TargetQuery}). A relative target resolves against
   * `relativeTo` as `RouterLink` would, or against the root without one.
   */
  link(target: InAppLink, query?: TargetQuery | null, relativeTo?: ActivatedRoute | null): UrlTree {
    let path: InAppLink = target;
    let embedded: Params = {};
    let fragment: string | undefined;
    if (typeof target === 'string') {
      const cut = target.search(/[?#]/);
      if (cut >= 0) {
        const tail = this.router.parseUrl(`/${target.slice(cut)}`);
        embedded = tail.queryParams;
        fragment = tail.fragment ?? undefined;
        path = target.slice(0, cut);
      }
    }
    const rewritten = this.policy.path(path);
    const commands = typeof rewritten === 'string' ? (rewritten === '' ? [] : [rewritten]) : [...rewritten];
    return this.router.createUrlTree(commands, {
      relativeTo: relativeTo ?? undefined,
      queryParams: composeQuery(this.policy.query(), embedded, query ?? {}),
      fragment,
    });
  }
}

/** `scope` overlaid by each target query in turn: a value sets its key, `null` removes it, `undefined` leaves it. */
function composeQuery(scope: Params, ...targets: TargetQuery[]): Params {
  const merged: Params = { ...scope };
  for (const target of targets) {
    for (const [key, value] of Object.entries(target)) {
      if (value === null) delete merged[key];
      else if (value !== undefined) merged[key] = value;
    }
  }
  return merged;
}

/**
 * `[routerLink]="'/board' | appLink: { chunk: id }"` — a cross-page link through {@link AppScope.link}, so it keeps the
 * app's scope. The anchor binds no sibling `queryParams`, `queryParamsHandling` or `fragment`: the target's query is
 * the pipe's argument. A relative target resolves against the anchor's route.
 *
 * Impure, because the scope is the URL's and moves without the link changing; an unchanged answer is handed back as the
 * same `UrlTree`, so a binding sees a new link only when it actually moved. A `null` or `undefined` target comes back
 * as it is, which `RouterLink` reads as no link at all.
 */
@Pipe({ name: 'appLink', pure: false })
export class AppLink implements PipeTransform {
  private readonly scope = inject(AppScope);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute, { optional: true });
  private last: { tree: UrlTree; url: string } | null = null;

  transform(target: InAppLink, query?: TargetQuery | null): UrlTree;
  transform(target: InAppLink | null | undefined, query?: TargetQuery | null): UrlTree | null | undefined;
  transform(target: InAppLink | null | undefined, query?: TargetQuery | null): UrlTree | null | undefined {
    if (target === null || target === undefined) return target;
    const tree = this.scope.link(target, query, this.route);
    const url = this.router.serializeUrl(tree);
    if (this.last?.url === url) return this.last.tree;
    this.last = { tree, url };
    return tree;
  }
}

/**
 * `[routerLink]="item.route | appPath" queryParamsHandling="merge"` — path policy only, through {@link AppScope.path},
 * for an anchor that deliberately keeps the current query. Every other link is an {@link AppLink}.
 *
 * Impure for the same reason as {@link AppLink}, and as stable: an unchanged answer is handed back as the same value.
 */
@Pipe({ name: 'appPath', pure: false })
export class AppPath implements PipeTransform {
  private readonly scope = inject(AppScope);
  private last: InAppLink | null = null;

  transform(link: InAppLink): InAppLink;
  transform(link: InAppLink | null | undefined): InAppLink | null | undefined;
  transform(link: InAppLink | null | undefined): InAppLink | null | undefined {
    if (link === null || link === undefined) return link;
    const next = this.scope.path(link);
    if (this.last !== null && sameLink(this.last, next)) return this.last;
    this.last = next;
    return next;
  }
}

function sameLink(a: InAppLink, b: InAppLink): boolean {
  if (typeof a === 'string' || typeof b === 'string') return a === b;
  return a.length === b.length && a.every((part, index) => part === b[index]);
}
