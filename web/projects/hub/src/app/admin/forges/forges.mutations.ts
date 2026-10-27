import { inject } from '@angular/core';
import { injectMutation, QueryClient } from '@tanstack/angular-query-experimental';
import {
  createForgeApiForgesPost, enableForgeApiForgesForgeEnablePost, hubConfigKey,
  patchForgeApiForgesForgePatch, retireForgeApiForgesForgeRetirePost,
  type ForgeCreateRequest, type ForgePatchRequest,
} from 'fleet';

export interface ForgeEditVars {
  readonly forgeId: string;
  readonly revision: number;
  readonly body: ForgePatchRequest;
}

export interface ForgeLifecycleVars {
  readonly forgeId: string;
  readonly revision: number;
  readonly retired: boolean;
}

export function injectCreateForgeMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (body: ForgeCreateRequest): Promise<void> => {
      const { error } = await createForgeApiForgesPost({ body, throwOnError: false });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}

export function injectEditForgeMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: ForgeEditVars): Promise<void> => {
      const { error } = await patchForgeApiForgesForgePatch({
        path: { forge: vars.forgeId }, headers: { 'if-match': vars.revision }, body: vars.body, throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}

export function injectForgeLifecycleMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: ForgeLifecycleVars): Promise<void> => {
      const call = vars.retired ? retireForgeApiForgesForgeRetirePost : enableForgeApiForgesForgeEnablePost;
      const { error } = await call({
        path: { forge: vars.forgeId }, headers: { 'if-match': vars.revision }, throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}
