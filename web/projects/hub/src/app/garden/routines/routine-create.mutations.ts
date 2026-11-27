import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';
import { hubApi } from 'fleet/hub-api';
import { gardenProject } from '../core/garden-project';
import { hubRoutinesKey } from '../../core/query-keys';
import { injectProjectFilter } from '../../core/project-filter';

export function injectCreateRoutineMutation() {
  const client = inject(QueryClient);
  const project = injectProjectFilter();
  return injectMutation(() => ({
    mutationFn: async (body: hubApi.RoutineCreateRequest) => {
      const { data, error } = await hubApi.createProjectRoutineApiProjectsProjectRoutinesPost({ path: { project: gardenProject(project()) }, body, throwOnError: false });
      if (error) throw error;
      return data!;
    },
    onSettled: () => client.invalidateQueries({ queryKey: hubRoutinesKey }),
  }));
}
