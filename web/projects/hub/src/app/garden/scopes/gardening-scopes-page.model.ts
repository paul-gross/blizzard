import type { RunRowView, ScopeView } from 'fleet';
import type { RoutineWindow } from '../core/routine-window';
import type { ScopeRowVm, ScopeSweptVm } from './scope-list';

/** The effective selection: `scopeSlug` while it still names a loaded scope, else
 * `null` — so a stale or mistyped route param highlights no row. */
export function presentScopeSlug(scopeSlug: string | null, scopes: readonly ScopeView[]): string | null {
  if (scopeSlug === null) return null;
  return scopes.some((s) => s.slug === scopeSlug) ? scopeSlug : null;
}

/** When each scope a run in `runs` names was last swept: the newest `minted_at` among the runs naming it. */
function lastSweptBySlug(runs: readonly RunRowView[]): ReadonlyMap<string, string> {
  const lastSwept = new Map<string, string>();
  for (const run of runs) {
    const seen = lastSwept.get(run.scope_slug);
    if (seen === undefined || Date.parse(run.minted_at) > Date.parse(seen)) lastSwept.set(run.scope_slug, run.minted_at);
  }
  return lastSwept;
}

/**
 * What a scope shows for a window holding no sweep of it. The window bounds the runs read, so an older sweep may exist:
 * the text says only that none fell inside it, never that the scope was never swept.
 */
export function notSweptLabel(window: RoutineWindow): string {
  return `not in ${window.days} days`;
}

/**
 * When a run last swept the scope `slug`, as the runs read over `window` can tell it — `null` until that read answers,
 * so the "not swept" text never shows for a read still in flight.
 */
export function scopeSwept(slug: string, runs: readonly RunRowView[] | undefined, window: RoutineWindow): ScopeSweptVm | null {
  return sweptFrom(runs === undefined ? null : lastSweptBySlug(runs), slug, window);
}

function sweptFrom(lastSwept: ReadonlyMap<string, string> | null, slug: string, window: RoutineWindow): ScopeSweptVm | null {
  return lastSwept === null ? null : { at: lastSwept.get(slug) ?? null, notSwept: notSweptLabel(window) };
}

/**
 * The scope list's rows: each scope's slug, description and when a run in the window last swept it ({@link scopeSwept}).
 */
export function scopeRows(
  scopes: readonly ScopeView[],
  runs: readonly RunRowView[] | undefined,
  window: RoutineWindow,
): readonly ScopeRowVm[] {
  const lastSwept = runs === undefined ? null : lastSweptBySlug(runs);
  return scopes.map((s) => ({
    slug: s.slug,
    description: s.description,
    retired: s.retired ?? false,
    swept: sweptFrom(lastSwept, s.slug, window),
  }));
}
