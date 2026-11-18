// exemplar: bzh:frontend-domain-boundary — the cache keeps the wire response; its readers take the domain cadence.

import { injectQuery } from '@tanstack/angular-query-experimental';

import { type CadenceView, listCadencesApiCadencesGet } from 'fleet';
import { hubCadenceEditListKey } from '../core/query-keys';
import type { Cadence } from './cadence.domain';
import { fromCadenceView } from './cadence.translate';

/** `GET /api/cadences` — the tenant's cadences, read as {@link Cadence}. */
export function injectCadencesQuery() {
  return injectQuery(() => ({
    queryKey: hubCadenceEditListKey,
    queryFn: async (): Promise<CadenceView[]> => {
      const { data, error } = await listCadencesApiCadencesGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
    select: (wire: CadenceView[]): readonly Cadence[] => wire.map(fromCadenceView),
  }));
}
