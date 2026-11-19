import { injectInfiniteQuery, injectQuery } from '@tanstack/angular-query-experimental';
import { hubSettingsHistoryKey, hubSettingsKey } from '../../core/query-keys';
import { changesApiAdminSettingsChangesGet, getSettingsApiAdminSettingsGet } from 'fleet';

export function injectHubSettingsQuery(enabled: () => boolean) {
  return injectQuery(() => ({
    queryKey: hubSettingsKey, enabled: enabled(),
    queryFn: async () => { const { data, error } = await getSettingsApiAdminSettingsGet(); if (error) throw error; return data!; },
  }));
}
export function injectHubSettingsHistoryQuery(enabled: () => boolean) {
  return injectInfiniteQuery(() => ({
    queryKey: hubSettingsHistoryKey, enabled: enabled(), initialPageParam: undefined as number | undefined,
    queryFn: async ({ pageParam }: { pageParam: number | undefined }) => {
      const { data, error } = await changesApiAdminSettingsChangesGet({ query: { before: pageParam } });
      if (error) throw error; return data!;
    },
    getNextPageParam: (last) => last.next_before ?? undefined,
  }));
}
