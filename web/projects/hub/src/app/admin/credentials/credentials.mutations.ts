import { inject } from '@angular/core';
import { injectMutation, QueryClient } from '@tanstack/angular-query-experimental';
import {
  createCredentialApiCredentialsPost, enableCredentialApiCredentialsCredentialEnablePost, hubConfigKey,
  patchCredentialApiCredentialsCredentialPatch, retireCredentialApiCredentialsCredentialRetirePost,
  type CredentialCreateRequest, type CredentialPatchRequest,
} from 'fleet';

export interface CredentialEditVars {
  readonly credentialId: string;
  readonly revision: number;
  readonly body: CredentialPatchRequest;
}

export interface CredentialLifecycleVars {
  readonly credentialId: string;
  readonly revision: number;
  readonly retired: boolean;
}

export function injectCreateCredentialMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (body: CredentialCreateRequest): Promise<void> => {
      const { error } = await createCredentialApiCredentialsPost({ body, throwOnError: false });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}

export function injectEditCredentialMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: CredentialEditVars): Promise<void> => {
      const { error } = await patchCredentialApiCredentialsCredentialPatch({
        path: { credential: vars.credentialId }, headers: { 'if-match': vars.revision }, body: vars.body, throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}

export function injectCredentialLifecycleMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: CredentialLifecycleVars): Promise<void> => {
      const call = vars.retired ? retireCredentialApiCredentialsCredentialRetirePost : enableCredentialApiCredentialsCredentialEnablePost;
      const { error } = await call({
        path: { credential: vars.credentialId }, headers: { 'if-match': vars.revision }, throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}
