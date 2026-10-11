import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  createProjectApiProjectsPost,
  hubConfigKey,
  linkRepositoryApiProjectsProjectRepositoryLinksRepositoryPut,
  linkSourceApiProjectsProjectSourceLinksSourcePut,
  patchProjectApiProjectsProjectPatch,
  type ProjectCreateRequest,
  type ProjectPatchRequest,
  type ProjectView,
  unlinkRepositoryApiProjectsProjectRepositoryLinksRepositoryRetirePost,
  unlinkSourceApiProjectsProjectSourceLinksSourceRetirePost,
} from 'fleet';
import { createProjectMutationKey, editProjectMutationKey, projectLinkMutationKey } from '../../core/mutation-keys';

/** A project edit — the sparse patch against the shown `revision`. */
export interface ProjectEditVars {
  readonly projectId: string;
  readonly revision: number;
  readonly body: ProjectPatchRequest;
}

/** What a project links: the work sources it draws from, or the repositories it may land in. */
export type ProjectLinkKind = 'source' | 'repository';

/** Link or unlink one record. `revision` is the link's own, sent as `If-Match` on an unlink;
 * a first link has none. */
export interface ProjectLinkVars {
  readonly projectId: string;
  readonly kind: ProjectLinkKind;
  readonly name: string;
  readonly linked: boolean;
  readonly revision: number | null;
}

/** `POST /api/projects` — creates a project and answers it. Settles by invalidating every
 * config read: a create also moves the change log and the projects every lens offers. */
export function injectCreateProjectMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: createProjectMutationKey,
    mutationFn: async (body: ProjectCreateRequest): Promise<ProjectView> => {
      const { data, error } = await createProjectApiProjectsPost({ body, throwOnError: false });
      if (error) throw error;
      return data!;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}

/** `PATCH /api/projects/{project}` with `If-Match` — addressed by id, so a slug change in the
 * same patch never moves the address. A stale revision is a 409 the caller shows. */
export function injectEditProjectMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: editProjectMutationKey,
    mutationFn: async (vars: ProjectEditVars): Promise<ProjectView> => {
      const { data, error } = await patchProjectApiProjectsProjectPatch({
        path: { project: vars.projectId },
        headers: { 'if-match': vars.revision },
        body: vars.body,
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}

/** `PUT …/{source-links|repository-links}/{name}` to link, `POST …/{name}/retire` to unlink.
 * Linking an unlinked record relinks it. */
export function injectProjectLinkMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: projectLinkMutationKey,
    mutationFn: async (vars: ProjectLinkVars): Promise<void> => {
      const headers = vars.revision === null ? undefined : { 'if-match': vars.revision };
      const { error } = await linkCall(vars, headers);
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubConfigKey }),
  }));
}

function linkCall(vars: ProjectLinkVars, headers: { 'if-match': number } | undefined) {
  if (vars.kind === 'source') {
    const path = { project: vars.projectId, source: vars.name };
    return vars.linked
      ? linkSourceApiProjectsProjectSourceLinksSourcePut({ path, throwOnError: false })
      : unlinkSourceApiProjectsProjectSourceLinksSourceRetirePost({ path, headers, throwOnError: false });
  }
  const path = { project: vars.projectId, repository: vars.name };
  return vars.linked
    ? linkRepositoryApiProjectsProjectRepositoryLinksRepositoryPut({ path, throwOnError: false })
    : unlinkRepositoryApiProjectsProjectRepositoryLinksRepositoryRetirePost({ path, headers, throwOnError: false });
}
