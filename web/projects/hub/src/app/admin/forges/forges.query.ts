import { injectQuery } from '@tanstack/angular-query-experimental';
import { getForgeApiForgesForgeGet, hubConfigListKey, hubConfigRecordKey, listForgesApiForgesGet, type ForgeDetail, type ForgeSummary } from 'fleet';

const NOUN = 'forges';

export function injectForgesQuery(includeRetired: () => boolean) {
  return injectQuery(() => {
    const retired = includeRetired();
    return {
      queryKey: hubConfigListKey(NOUN, retired),
      queryFn: async (): Promise<ForgeSummary[]> => {
        const { data, error } = await listForgesApiForgesGet({ query: { include_retired: retired }, throwOnError: false });
        if (error) throw error;
        return data?.forges ?? [];
      },
    };
  });
}

export function injectForgeQuery(key: () => string | null) {
  return injectQuery(() => {
    const forge = key();
    return {
      queryKey: hubConfigRecordKey(NOUN, forge),
      enabled: forge !== null,
      queryFn: async (): Promise<ForgeDetail> => {
        const { data, error } = await getForgeApiForgesForgeGet({ path: { forge: forge! }, throwOnError: false });
        if (error) throw error;
        return data!;
      },
    };
  });
}
