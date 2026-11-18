import { injectQuery } from '@tanstack/angular-query-experimental';
import { getUrgencyApiUrgenciesKeyGet, type UrgencyView } from 'fleet';
import { URGENCIES_POLL_MS } from '../../core/urgencies.query';
import { fromUrgencyView } from '../../core/urgency.translate';

function urgencyQueryOptions(value: string | null) {
  return {
      queryKey: hubConfigRecordKey('urgencies', value), enabled: value !== null,
      refetchInterval: URGENCIES_POLL_MS, refetchOnWindowFocus: true, refetchOnReconnect: true,
      queryFn: async (): Promise<UrgencyView> => {
        const { data, error } = await getUrgencyApiUrgenciesKeyGet({ path: { key: value! }, throwOnError: false });
        if (error) throw error;
        return data!;
      },
  };
}

export function domainUrgencyQueryOptions(value: string | null) {
  return { ...urgencyQueryOptions(value), select: fromUrgencyView };
}

export function injectDomainUrgencyQuery(key: () => string | null) {
  return injectQuery(() => domainUrgencyQueryOptions(key()));
}

import { getSettingsApiSettingsGet, type SettingsView } from 'fleet';
import { hubConfigRecordKey } from '../../core/query-keys';

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
