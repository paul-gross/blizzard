import { computed, inject, type Signal } from '@angular/core';

import { injectLensedProject } from '../../core/lensed-project';
import { ProjectLens } from '../../core/project-lens';
import type { ProjectLinkKind } from './projects.mutations';
import { linkedKeys } from './projects.model';
import { injectProjectRepositoryLinksQuery, injectProjectSourceLinksQuery } from './projects.query';

/** A tenant list under the project lens: on All every record, under a project the ones it links. */
export interface LensLinks {
  /** The keys the lensed project links — a source's or a repository's id, or `null` on All, where nothing is narrowed. */
  readonly names: Signal<ReadonlySet<string> | null>;
  /** Whether the lensed project's links are still loading, so the narrowed list is not known yet. */
  readonly pending: Signal<boolean>;
  /** Whether reading the lensed project's links failed. */
  readonly failed: Signal<boolean>;
  /** The list's caption: `base` on All, and which project narrows it under a lens. */
  caption(base: string): string;
  /** What an empty list says: `fallback` on All, and that the project links none of `noun` under a lens. */
  emptyText(fallback: string, noun: string): string;
}

/** The lensed project's `kind` links, read only while the lens is on a project. */
export function injectLensLinks(kind: ProjectLinkKind): LensLinks {
  const lens = inject(ProjectLens);
  const project = injectLensedProject();
  // The link routes resolve a project by its slug as by its id, so the lens's slug addresses the read.
  const query = kind === 'source' ? injectProjectSourceLinksQuery(lens.slug) : injectProjectRepositoryLinksQuery(lens.slug);
  const names = computed(() => {
    if (!lens.lensed()) return null;
    return linkedKeys(query.data() ?? []);
  });
  /** The lensed project's name, its slug until the projects read holds it. */
  const lensName = (): string => project()?.name ?? lens.slug() ?? '';
  return {
    names,
    pending: computed(() => lens.lensed() && query.status() === 'pending'),
    failed: computed(() => lens.lensed() && query.status() === 'error'),
    caption: (base) => (lens.lensed() ? `${base} · linked by ${lensName()}` : base),
    emptyText: (fallback, noun) =>
      !lens.lensed() || (names()?.size ?? 0) > 0 ? fallback : `${lensName()} links no ${noun}.`,
  };
}

/** The records whose `keyOf` the lens keeps — every one when `keys` is `null`. */
export function withinLens<T>(
  records: readonly T[],
  keyOf: (record: T) => string,
  keys: ReadonlySet<string> | null,
): readonly T[] {
  return keys === null ? records : records.filter((record) => keys.has(keyOf(record)));
}
