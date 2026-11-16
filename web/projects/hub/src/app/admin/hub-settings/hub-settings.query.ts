import { injectInfiniteQuery, injectQuery } from '@tanstack/angular-query-experimental';
import { changesApiAdminSettingsChangesGet, getSettingsApiAdminSettingsGet } from 'fleet';

export const settingsKey = ['hub', 'admin-settings'] as const;
export const historyKey = ['hub', 'admin-settings-history'] as const;

export function injectHubSettingsQuery(enabled: () => boolean) {
  return injectQuery(() => ({
    queryKey: settingsKey, enabled: enabled(),
    queryFn: async () => { const { data, error } = await getSettingsApiAdminSettingsGet(); if (error) throw error; return data!; },
  }));
}
export function injectHubSettingsHistoryQuery(enabled: () => boolean) {
  return injectInfiniteQuery(() => ({
    queryKey: historyKey, enabled: enabled(), initialPageParam: undefined as number | undefined,
    queryFn: async ({ pageParam }: { pageParam: number | undefined }) => {
      const { data, error } = await changesApiAdminSettingsChangesGet({ query: { before: pageParam } });
      if (error) throw error; return data!;
    },
    getNextPageParam: (last) => last.next_before ?? undefined,
  }));
}
