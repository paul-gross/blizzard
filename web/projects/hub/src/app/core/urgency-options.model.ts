import type { KitChipOption, Urgency } from 'fleet';

/** How a picker treats retired urgencies: drop them, keep them, or keep only
 * the one its current value names (by id or by slug). */
export type RetiredUrgencies = 'exclude' | 'include' | { readonly current: string | null };

export interface UrgencyOptionsConfig {
  readonly key: 'id' | 'slug';
  readonly retired: RetiredUrgencies;
  /** A leading empty-value row, labelled `label (name)`, or bare `label` when the name is null. */
  readonly inherit?: { readonly label: string; readonly name: string | null };
}

/** The options of an urgency picker, in the order the urgencies arrive. */
export function urgencyOptions(urgencies: readonly Urgency[], config: UrgencyOptionsConfig): KitChipOption[] {
  const { key, retired, inherit } = config;
  const kept = urgencies.filter((u) => {
    if (!u.retired || retired === 'include') return true;
    if (retired === 'exclude') return false;
    return u.slug === retired.current || u.id === retired.current;
  });
  const options = kept.map((u) => ({ value: u[key], label: u.name }));
  if (!inherit) return options;
  return [{ value: '', label: inherit.name === null ? inherit.label : `${inherit.label} (${inherit.name})` }, ...options];
}

/** The name of the tenant's default urgency, or null when none is marked default. */
export function defaultUrgencyName(urgencies: readonly Urgency[]): string | null {
  return urgencies.find((u) => u.default)?.name ?? null;
}
