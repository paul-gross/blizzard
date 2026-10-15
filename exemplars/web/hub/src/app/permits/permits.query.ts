// exemplar: bzh:generated-client, bzh:frontend-wire-conformist — a data-access file over the generated client.

import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  type PermitView,
  getPermitApiPermitsPermitIdGet,
  hubPermitKey,
  hubPermitsKey,
  listPermitsApiPermitsGet,
} from 'fleet';
import { injectProjectFilter, lensedKey } from 'fleet/shell';
import { DRAIN_LIMIT, drainPages } from '../core/paginated-read';

/** `GET /api/permits` — the lensed project's permits, newest requested first, narrowed to `holder()` when named. */
export function injectPermitsQuery(holder: () => string | null) {
  const filter = injectProjectFilter();
  return injectQuery(() => {
    const named = holder();
    const project = filter();
    return {
      queryKey: lensedKey([...hubPermitsKey, named], project),
      queryFn: (): Promise<PermitView[]> =>
        drainPages(
          (cursor) =>
            listPermitsApiPermitsGet({
              query: { project, holder: named, cursor, limit: DRAIN_LIMIT },
              throwOnError: false,
            }),
          (page) => page.permits,
        ),
    };
  });
}

/** `GET /api/permits/{permit_id}` — one permit, at rest while `permitId()` is `null`. */
export function injectPermitQuery(permitId: () => string | null) {
  return injectQuery(() => {
    const id = permitId();
    return {
      queryKey: hubPermitKey(id),
      enabled: id !== null,
      queryFn: async (): Promise<PermitView> => {
        const { data, error } = await getPermitApiPermitsPermitIdGet({ path: { permit_id: id! }, throwOnError: false });
        if (error) throw error;
        return data!;
      },
    };
  });
}
