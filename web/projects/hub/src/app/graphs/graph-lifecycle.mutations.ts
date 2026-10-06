import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { enableGraphApiGraphsGraphIdEnablePost, retireGraphApiGraphsGraphIdRetirePost, hubGraphKey, hubGraphsKey } from 'fleet';
import { graphLifecycleMutationKey } from '../core/mutation-keys';

/** Retire or re-enable a graph's reversible lifecycle brake. */
export interface GraphLifecycleVars {
  readonly graphId: string;
  readonly retired: boolean;
}

/**
 * `POST /api/graphs/{id}/retire|enable` — routed by the desired `retired` state
 * (mirrors `injectChunkPauseMutation`), through the generated client
 * (bzh:generated-client). On success it re-reads the graph list and this graph's own
 * detail, since retiring/enabling can flip which version of its name is `effective`.
 * The hub records the authenticated caller; the request carries no actor.
 */
export function injectGraphLifecycleMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: graphLifecycleMutationKey,
    mutationFn: async (vars: GraphLifecycleVars): Promise<void> => {
      const call = vars.retired ? retireGraphApiGraphsGraphIdRetirePost : enableGraphApiGraphsGraphIdEnablePost;
      const { error } = await call({
        path: { graph_id: vars.graphId },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubGraphsKey }),
        queryClient.invalidateQueries({ queryKey: hubGraphKey(vars.graphId) }),
      ]),
  }));
}
