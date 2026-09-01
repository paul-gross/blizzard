import { injectQuery } from '@tanstack/angular-query-experimental';

import { type RoutineBaselineView, routineBaselinesApiRoutinesRoutineIdBaselinesGet } from '../api/hub';
import { hubRoutineBaselinesKey } from '../query-keys';

/**
 * Hub `GET /api/routines/{routine_id}/baselines` read — every scope a routine has
 * swept (D5); absence means never swept.
 *
 * Reactive over the selected routine id, exactly like {@link injectHubChunkDetailQuery}:
 * pass an accessor — the query re-keys as the selection changes and disables itself
 * (`enabled`) while no routine is selected, so no request fires before the dialog
 * knows which routine it's running.
 */
export function injectHubRoutineBaselinesQuery(routineId: () => string | undefined) {
  return injectQuery(() => {
    const id = routineId();
    return {
      queryKey: hubRoutineBaselinesKey(id ?? ''),
      enabled: id !== undefined,
      queryFn: async (): Promise<RoutineBaselineView[]> => {
        const { data, error } = await routineBaselinesApiRoutinesRoutineIdBaselinesGet({
          path: { routine_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data ?? [];
      },
    };
  });
}
