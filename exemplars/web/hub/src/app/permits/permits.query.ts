// exemplar: bzh:generated-client, bzh:frontend-domain-boundary — a data-access file over the generated client.

import { injectQuery } from '@tanstack/angular-query-experimental';

import { type PermitView, getPermitApiPermitsPermitIdGet, listPermitsApiPermitsGet } from 'fleet/hub-api';
import { DRAIN_LIMIT, drainPages } from '../core/paginated-read';
import { injectProjectFilter } from '../core/project-filter';
import { fromPermitView } from './permits.translate';
import { hubPermitKey, hubPermitsKey, lensedKey } from '../core/query-keys';

/** `GET /api/permits` — the lensed project's permits, newest requested first, narrowed to `holder()` when named. */
export function injectPermitsQuery(holder: () => string | null) {
  const filter = injectProjectFilter();
  return injectQuery(() => {
    const named = holder();
    const project = filter();
    return {
      queryKey: lensedKey([...hubPermitsKey, named], project),
      select: (permits: PermitView[]) => permits.map(fromPermitView),
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
      select: fromPermitView,
      queryFn: async (): Promise<PermitView> => {
        const { data, error } = await getPermitApiPermitsPermitIdGet({ path: { permit_id: id! }, throwOnError: false });
        if (error) throw error;
        return data!;
      },
    };
  });
}
