import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  getSecretApiSecretsNameGet,
  hubConfigListKey,
  hubConfigRecordKey,
  listSecretsApiSecretsGet,
  type SecretView,
} from 'fleet';

const NOUN = 'secrets';

/** `GET /api/secrets` — every secret, retired ones too while
 * `includeRetired()` holds. No SSE event names a config change, so a write made
 * through another door shows on the next refetch. */
export function injectSecretsQuery(includeRetired: () => boolean) {
  return injectQuery(() => {
    const retired = includeRetired();
    return {
      queryKey: hubConfigListKey(NOUN, retired),
      queryFn: async (): Promise<SecretView[]> => {
        const { data, error } = await listSecretsApiSecretsGet({
          query: { include_retired: retired },
          throwOnError: false,
        });
        if (error) throw error;
        return data ?? [];
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
      queryFn: async (): Promise<SecretView> => {
        const { data, error } = await getSecretApiSecretsNameGet({
          path: { name: recordName! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
    };
  });
}
