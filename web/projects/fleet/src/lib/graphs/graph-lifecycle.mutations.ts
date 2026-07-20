import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { enableGraphApiGraphsGraphIdEnablePost, retireGraphApiGraphsGraphIdRetirePost } from '../api/hub';
import { hubGraphKey, hubGraphsKey } from '../query-keys';

/** Retire or re-enable a graph's reversible lifecycle brake (issue #101): a retired
 * graph is excluded from name resolution and refuses new re-pins, but the `graphs`
 * row itself is never touched — the immutable definition survives unchanged. */
export interface GraphLifecycleVars {
  readonly graphId: string;
  readonly retired: boolean;
}

/**
 * `POST /api/graphs/{id}/retire|enable` — routed by the desired `retired` state
 * (mirrors `injectChunkPauseMutation`), through the generated client
 * (bzh:generated-client). On success it re-reads the graph list and this graph's own
 * detail, since retiring/enabling can flip which version of its name is `effective`.
 * `by` defaults to `operator` server-side.
 */
export function injectGraphLifecycleMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: GraphLifecycleVars): Promise<void> => {
      const call = vars.retired ? retireGraphApiGraphsGraphIdRetirePost : enableGraphApiGraphsGraphIdEnablePost;
      const { error } = await call({
        path: { graph_id: vars.graphId },
        body: { by: 'operator' },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSuccess: (_data, vars) => {
      void queryClient.invalidateQueries({ queryKey: hubGraphsKey });
      void queryClient.invalidateQueries({ queryKey: hubGraphKey(vars.graphId) });
    },
  }));
}
