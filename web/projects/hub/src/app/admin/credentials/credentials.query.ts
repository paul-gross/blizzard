import { injectQuery } from '@tanstack/angular-query-experimental';
import {
  type CredentialDocument,
  type CredentialsListView,
  getCredentialApiCredentialsCredentialGet,
  hubConfigListKey,
  hubConfigRecordKey,
  listCredentialsApiCredentialsGet,
} from 'fleet';

const NOUN = 'credentials';

/** `GET /api/credentials` — every credential, retired ones too while `includeRetired()` holds, with each
 * one's referrer count and the bindings a credential may name. Freshness: `hubConfigKey`
 * (`web/projects/fleet/src/lib/core/query-keys.ts`). */
export function injectCredentialsQuery(includeRetired: () => boolean) {
  return injectQuery(() => {
    const retired = includeRetired();
    return {
      queryKey: hubConfigListKey(NOUN, retired),
      queryFn: async (): Promise<CredentialsListView> => {
        const { data, error } = await listCredentialsApiCredentialsGet({ query: { include_retired: retired }, throwOnError: false });
        if (error) throw error;
        return data ?? { credentials: [], bindings: [], forge_bindings: [] };
      },
    };
  });
}

/** `GET /api/credentials/{credential}` — by id, slug, or former slug, with the records naming it. */
export function injectCredentialQuery(key: () => string | null) {
  return injectQuery(() => {
    const credential = key();
    return {
      queryKey: hubConfigRecordKey(NOUN, credential),
      enabled: credential !== null,
      queryFn: async (): Promise<CredentialDocument> => {
        const { data, error } = await getCredentialApiCredentialsCredentialGet({ path: { credential: credential! }, throwOnError: false });
        if (error) throw error;
        return data!;
      },
    };
  });
}
