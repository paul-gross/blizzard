import { inject } from '@angular/core';
import { injectMutation, QueryClient } from '@tanstack/angular-query-experimental';
import {
  createUrgencyApiUrgenciesPost, patchUrgencyApiUrgenciesKeyPatch, enableUrgencyApiUrgenciesKeyEnablePost,
  retireUrgencyApiUrgenciesKeyRetirePost, positionUrgencyApiUrgenciesOrderPositionPost, patchSettingsApiSettingsPatch,
  hubConfigKey, type UrgencyCreateRequest, type UrgencyPatchRequest, type UrgencyPositionRequest,
} from 'fleet';

export const urgencyPositionMutationKey = ['hub', 'urgencies', 'position'] as const;
export const urgencyDefaultMutationKey = ['hub', 'urgencies', 'default'] as const;

export function injectCreateUrgencyMutation() {
  const client = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (body: UrgencyCreateRequest) => {
      const { data, error } = await createUrgencyApiUrgenciesPost({ body, throwOnError: false });
      if (error) throw error;
      return data!;
    },
    onSettled: () => client.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}
export function injectEditUrgencyMutation() {
  const client = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (v: { key: string; revision: number; body: UrgencyPatchRequest }) => {
      const { error } = await patchUrgencyApiUrgenciesKeyPatch({ path: { key: v.key }, headers: { 'if-match': v.revision }, body: v.body, throwOnError: false });
      if (error) throw error;
    },
    onSettled: () => client.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}
export function injectUrgencyLifecycleMutation() {
  const client = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (v: { key: string; revision: number; retired: boolean; moveTo?: string }) => {
      const common = { path: { key: v.key }, headers: { 'if-match': v.revision }, throwOnError: false as const };
      const { error } = v.retired
        ? await retireUrgencyApiUrgenciesKeyRetirePost({ ...common, body: v.moveTo ? { move_to: v.moveTo } : {} })
        : await enableUrgencyApiUrgenciesKeyEnablePost(common);
      if (error) throw error;
    },
    onSettled: () => client.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}
export function injectPositionUrgencyMutation() {
  const client = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: urgencyPositionMutationKey,
    mutationFn: async (body: UrgencyPositionRequest) => {
      const { error } = await positionUrgencyApiUrgenciesOrderPositionPost({ body, throwOnError: false });
      if (error) throw error;
    },
    onSettled: () => client.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}
export function injectDefaultUrgencyMutation() {
  const client = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: urgencyDefaultMutationKey,
    mutationFn: async (v: { key: string; revision: number }) => {
      const { error } = await patchSettingsApiSettingsPatch({ body: { default_urgency: v.key }, headers: { 'if-match': v.revision }, throwOnError: false });
      if (error) throw error;
    },
    onSettled: () => client.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}
