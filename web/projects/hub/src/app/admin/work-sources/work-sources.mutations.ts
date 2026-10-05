import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  createWorkSourceApiWorkSourcesPost,
  enableWorkSourceApiWorkSourcesSourceEnablePost,
  hubConfigKey,
  patchWorkSourceApiWorkSourcesSourcePatch,
  retireWorkSourceApiWorkSourcesSourceRetirePost,
  type WorkSourceDocument,
  type WorkSourcePatchRequest,
} from 'fleet';
import {
  createWorkSourceMutationKey,
  editWorkSourceMutationKey,
  workSourceLifecycleMutationKey,
} from '../../core/mutation-keys';

/** A work source edit — the sparse patch against the shown `revision`. */
export interface WorkSourceEditVars {
  readonly name: string;
  readonly revision: number;
  readonly body: WorkSourcePatchRequest;
}

/** Retire or re-enable a work source at the shown `revision`. */
export interface WorkSourceLifecycleVars {
  readonly name: string;
  readonly revision: number;
  readonly retired: boolean;
}

/** `POST /api/work-sources` — creates a work source. Settles by invalidating every
 * config read: a write here also moves the change log and a secret's references. */
export function injectCreateWorkSourceMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: createWorkSourceMutationKey,
    mutationFn: async (body: WorkSourceDocument): Promise<void> => {
      const { error } = await createWorkSourceApiWorkSourcesPost({
        body,
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}

/** `PATCH /api/work-sources/{source}` with `If-Match` — a stale revision is a 409 the
 * caller shows; the settle refetches the record so the next save diffs against it. */
export function injectEditWorkSourceMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: editWorkSourceMutationKey,
    mutationFn: async (vars: WorkSourceEditVars): Promise<void> => {
      const { error } = await patchWorkSourceApiWorkSourcesSourcePatch({
        path: { source: vars.name },
        headers: { 'if-match': vars.revision },
        body: vars.body,
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}

/** `POST /api/work-sources/{source}/retire|enable` with `If-Match`, routed by the
 * desired `retired` state. */
export function injectWorkSourceLifecycleMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: workSourceLifecycleMutationKey,
    mutationFn: async (vars: WorkSourceLifecycleVars): Promise<void> => {
      const call = vars.retired
        ? retireWorkSourceApiWorkSourcesSourceRetirePost
        : enableWorkSourceApiWorkSourcesSourceEnablePost;
      const { error } = await call({
        path: { source: vars.name },
        headers: { 'if-match': vars.revision },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}
