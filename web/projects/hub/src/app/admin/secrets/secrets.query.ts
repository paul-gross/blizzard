import { injectQuery } from '@tanstack/angular-query-experimental';

import { getSecretApiSecretsNameGet, listSecretsApiSecretsGet } from 'fleet/hub-api';
import { hubConfigListKey, hubConfigRecordKey } from '../../core/query-keys';
import type { Secret } from './secret.domain';
import { fromSecretView } from './secret.translate';

const NOUN = 'secrets';

/** `GET /api/secrets` — every secret, retired ones too while
 * `includeRetired()` holds. Freshness: `hubConfigKey` (`web/projects/hub/src/app/core/query-keys.ts`). */
export function injectSecretsQuery(includeRetired: () => boolean) {
  return injectQuery(() => {
    const retired = includeRetired();
    return {
      queryKey: hubConfigListKey(NOUN, retired),
      queryFn: async (): Promise<Secret[]> => {
        const { data, error } = await listSecretsApiSecretsGet({
          query: { include_retired: retired },
          throwOnError: false,
        });
        if (error) throw error;
        return (data ?? []).map(fromSecretView);
      },
    };
  });
}

/** `GET /api/secrets/{name}` — one secret, at rest while `name()` is `null`. */
export function injectSecretQuery(name: () => string | null) {
  return injectQuery(() => {
    const recordName = name();
    return {
      queryKey: hubConfigRecordKey(NOUN, recordName),
      enabled: recordName !== null,
      queryFn: async (): Promise<Secret> => {
        const { data, error } = await getSecretApiSecretsNameGet({
          path: { name: recordName! },
          throwOnError: false,
        });
        if (error) throw error;
        return fromSecretView(data!);
      },
    };
  });
}
