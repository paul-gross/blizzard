import { injectQuery } from '@tanstack/angular-query-experimental';
import { getUrgencyApiUrgenciesKeyGet, hubConfigRecordKey, type UrgencyView } from 'fleet';
import { URGENCIES_POLL_MS } from '../../core/urgencies.query';

export function injectUrgencyQuery(key: () => string | null) {
  return injectQuery(() => {
    const value = key();
    return {
      queryKey: hubConfigRecordKey('urgencies', value), enabled: value !== null,
      refetchInterval: URGENCIES_POLL_MS, refetchOnWindowFocus: true, refetchOnReconnect: true,
      queryFn: async (): Promise<UrgencyView> => {
        const { data, error } = await getUrgencyApiUrgenciesKeyGet({ path: { key: value! }, throwOnError: false });
        if (error) throw error;
        return data!;
      },
    };
  });
}

import { getSettingsApiSettingsGet, type SettingsView } from 'fleet';

export function injectSettingsQuery() {
  return injectQuery(() => ({
    queryKey: ['hub', 'config', 'settings'],
    refetchInterval: URGENCIES_POLL_MS, refetchOnWindowFocus: true, refetchOnReconnect: true,
    queryFn: async (): Promise<SettingsView> => {
      const { data, error } = await getSettingsApiSettingsGet({ throwOnError: false });
      if (error) throw error;
      return data!;
    },
  }));
}
