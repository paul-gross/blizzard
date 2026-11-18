// exemplar: bzh:frontend-edit-baseline — the container's derivations, pure.

import type { Cadence } from './cadence.domain';

/** The cadence `slug` names, or `null` for a slug no loaded cadence holds. */
export function cadenceBySlug(slug: string | null, cadences: readonly Cadence[]): Cadence | null {
  return slug === null ? null : (cadences.find((c) => c.slug === slug) ?? null);
}

/** `tree` while an edit is under way — while `baseline` is held — and `null` otherwise. */
export function whileEditing<T>(baseline: unknown, tree: T): T | null {
  return baseline === null ? null : tree;
}
