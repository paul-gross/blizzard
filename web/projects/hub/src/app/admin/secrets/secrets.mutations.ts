import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  createSecretApiSecretsPost,
  enableSecretApiSecretsNameEnablePost,
  hubConfigKey,
  replaceSecretApiSecretsNameValuePut,
  retireSecretApiSecretsNameRetirePost,
} from 'fleet';
import {
  createSecretMutationKey,
  replaceSecretMutationKey,
  secretLifecycleMutationKey,
} from '../../core/mutation-keys';

/** A new secret — its name and its write-only value. */
export interface SecretCreateVars {
  readonly name: string;
  readonly value: string;
}

/** A secret's new value, against the shown `revision`. */
export interface SecretReplaceVars {
  readonly name: string;
  readonly revision: number;
  readonly value: string;
}

/** Retire or re-enable a secret. The wire takes no `If-Match` for either. */
export interface SecretLifecycleVars {
  readonly name: string;
  readonly retired: boolean;
}

/** `POST /api/secrets` — creates a secret. The value rides only this request body. */
export function injectCreateSecretMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: createSecretMutationKey,
    mutationFn: async (vars: SecretCreateVars): Promise<void> => {
      const { error } = await createSecretApiSecretsPost({
        body: vars,
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}

/** `PUT /api/secrets/{name}/value` with `If-Match` — replaces a secret's value. */
export function injectReplaceSecretMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: replaceSecretMutationKey,
    mutationFn: async (vars: SecretReplaceVars): Promise<void> => {
      const { error } = await replaceSecretApiSecretsNameValuePut({
        path: { name: vars.name },
        headers: { 'if-match': vars.revision },
        body: { value: vars.value },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}

/** `POST /api/secrets/{name}/retire|enable`, routed by the desired `retired` state. A
 * secret something live refers to refuses to retire with a 409 the caller shows. */
export function injectSecretLifecycleMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: secretLifecycleMutationKey,
    mutationFn: async (vars: SecretLifecycleVars): Promise<void> => {
      const call = vars.retired ? retireSecretApiSecretsNameRetirePost : enableSecretApiSecretsNameEnablePost;
      const { error } = await call({
        path: { name: vars.name },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}
