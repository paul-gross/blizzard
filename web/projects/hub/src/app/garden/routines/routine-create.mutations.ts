import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';
import { hubApi, hubRoutinesKey } from 'fleet';
import { injectProjectFilter } from 'fleet/shell';
import { gardenProject } from '../core/garden-project';

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
