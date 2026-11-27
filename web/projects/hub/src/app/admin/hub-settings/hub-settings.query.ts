import { type InfiniteData, injectInfiniteQuery, injectQuery } from '@tanstack/angular-query-experimental';
import { hubSettingsHistoryKey, hubSettingsKey } from '../../core/query-keys';
import { changesApiAdminSettingsChangesGet, getSettingsApiAdminSettingsGet } from 'fleet/hub-api';
import type { HubSettingsHistoryPage } from './hub-settings.domain';
import { toHubSettings, toHubSettingsHistoryPage } from './hub-settings.translate';

type HubSettingsChanges = NonNullable<Awaited<ReturnType<typeof changesApiAdminSettingsChangesGet>>['data']>;

export function injectHubSettingsQuery(enabled: () => boolean) {
  return injectQuery(() => ({
    queryKey: hubSettingsKey, enabled: enabled(),
    select: toHubSettings,
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
    select: (data: InfiniteData<HubSettingsChanges, number | undefined>): InfiniteData<HubSettingsHistoryPage, number | undefined> => ({
      pages: data.pages.map(toHubSettingsHistoryPage), pageParams: data.pageParams,
    }),
  }));
}
