import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  createRepositoryApiRepositoriesPost,
  enableRepositoryApiRepositoriesNameEnablePost,
  hubConfigKey,
  patchRepositoryApiRepositoriesNamePatch,
  retireRepositoryApiRepositoriesNameRetirePost,
  type RepositoryDocument,
  type RepositoryPatchRequest,
} from 'fleet';
import {
  createRepositoryMutationKey,
  editRepositoryMutationKey,
  repositoryLifecycleMutationKey,
} from '../../core/mutation-keys';

/** A repository edit — the sparse patch against the shown `revision`. */
export interface RepositoryEditVars {
  readonly name: string;
  readonly revision: number;
  readonly body: RepositoryPatchRequest;
}

/** Retire or re-enable a repository at the shown `revision`. */
export interface RepositoryLifecycleVars {
  readonly name: string;
  readonly revision: number;
  readonly retired: boolean;
}

/** `POST /api/repositories` — creates a repository. Settles by invalidating every
 * config read: a write here also moves the change log and a secret's references. */
export function injectCreateRepositoryMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: createRepositoryMutationKey,
    mutationFn: async (body: RepositoryDocument): Promise<void> => {
      const { error } = await createRepositoryApiRepositoriesPost({
        body,
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}

/** `PATCH /api/repositories/{name}` with `If-Match` — a stale revision is a 409 the
 * caller shows; the settle refetches the record so the next save diffs against it. */
export function injectEditRepositoryMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: editRepositoryMutationKey,
    mutationFn: async (vars: RepositoryEditVars): Promise<void> => {
      const { error } = await patchRepositoryApiRepositoriesNamePatch({
        path: { name: vars.name },
        headers: { 'if-match': vars.revision },
        body: vars.body,
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}

/** `POST /api/repositories/{name}/retire|enable` with `If-Match`, routed by the
 * desired `retired` state. */
export function injectRepositoryLifecycleMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: repositoryLifecycleMutationKey,
    mutationFn: async (vars: RepositoryLifecycleVars): Promise<void> => {
      const call = vars.retired
        ? retireRepositoryApiRepositoriesNameRetirePost
        : enableRepositoryApiRepositoriesNameEnablePost;
      const { error } = await call({
        path: { name: vars.name },
        headers: { 'if-match': vars.revision },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}
