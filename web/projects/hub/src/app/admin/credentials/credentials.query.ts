import { injectQuery } from '@tanstack/angular-query-experimental';
import {
  type CredentialDocument,
  type CredentialsListView,
  getCredentialApiCredentialsCredentialGet,
  listCredentialsApiCredentialsGet,
} from 'fleet';
import { hubConfigListKey, hubConfigRecordKey } from '../../core/query-keys';
import type { CredentialChoice } from '../repositories/repository.domain';
import { fromCredentialSummary } from '../repositories/repository.translate';
import { fromCredentialBinding, fromCredentialDocument, fromCredentialListItem, fromForgeBindings } from './credential.translate';

const NOUN = 'credentials';

/** `GET /api/credentials` — every credential, retired ones too while `includeRetired()` holds, with each
 * one's referrer count and the bindings a credential may name. Freshness: `hubConfigKey`
 * (`web/projects/hub/src/app/core/query-keys.ts`). */
export function injectCredentialsQuery(includeRetired: () => boolean) {
  return injectQuery(() => ({
    ...credentialsOptions(includeRetired()),
    select: (list: CredentialsListView) => ({
      credentials: list.credentials.map(fromCredentialListItem),
      bindings: list.bindings.map(fromCredentialBinding),
      forgeBindings: list.forge_bindings.map(fromForgeBindings),
    }),
  }));
}

/** {@link injectCredentialsQuery}'s read, on the same cache key, as the credentials a repository form may offer. */
export function injectCredentialChoicesQuery(includeRetired: () => boolean) {
  return injectQuery(() => ({
    ...credentialsOptions(includeRetired()),
    select: (list: CredentialsListView): readonly CredentialChoice[] => list.credentials.map(fromCredentialSummary),
  }));
}

function credentialsOptions(retired: boolean) {
  return {
    queryKey: hubConfigListKey(NOUN, retired),
    queryFn: async (): Promise<CredentialsListView> => {
      const { data, error } = await listCredentialsApiCredentialsGet({ query: { include_retired: retired }, throwOnError: false });
      if (error) throw error;
      return data ?? { credentials: [], bindings: [], forge_bindings: [] };
    },
  };
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
      select: fromCredentialDocument,
    };
  });
}
