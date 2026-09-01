import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  listRoutinesApiRoutinesGet,
  routineSweepsApiRoutinesRoutineIdSweepsGet,
  routineTrendApiRoutinesTrendGet,
  type GardenSweepsView,
  type RoutineView,
  type TrendView,
} from '../api/hub';
import { hubRoutineSweepsKey, hubRoutineTrendKey, hubRoutinesKey } from '../query-keys';

/**
 * Hub `GET /api/routines` read — every routine, newest first. Routines change rarely
 * and carry no SSE event of their own, `injectHubGraphsQuery`'s own standing.
 */
export function injectHubRoutinesQuery() {
  return injectQuery(() => ({
    queryKey: hubRoutinesKey,
    queryFn: async (): Promise<RoutineView[]> => {
      const { data, error } = await listRoutinesApiRoutinesGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
  }));
}

/**
 * Hub `GET /api/routines/trend` read — one routine's finding inflow against outflow
 * over `[since, until)`. Every argument is an accessor so the caller can recompute the
 * window without re-wiring the query (`injectHubFleetSpendQuery`'s own shape); disabled
 * while `routineName()` is `null` — the panel's own "nothing selected" rest state.
 */
export function injectHubRoutineTrendQuery(
  routineName: () => string | null,
  since: () => string,
  until: () => string,
  introducedBoundary: () => string,
  periodDays: () => number,
) {
  return injectQuery(() => {
    const name = routineName();
    return {
      queryKey: name === null ? hubRoutineTrendKey('', '', '', '', 0) : hubRoutineTrendKey(name, since(), until(), introducedBoundary(), periodDays()),
      enabled: name !== null,
      queryFn: async (): Promise<TrendView> => {
        const { data, error } = await routineTrendApiRoutinesTrendGet({
          query: {
            routine: name!,
            since: since(),
            until: until(),
            introduced_boundary: introducedBoundary(),
            period_days: periodDays(),
          },
          throwOnError: false,
        });
        if (error) throw error;
        return data as TrendView;
      },
    };
  });
}

/**
 * Hub `GET /api/routines/{routine_id}/sweeps` read — one routine's per-scope
 * last-swept table (unwindowed) and its measurement series over `[since, until)` (D2).
 * Disabled while `routineId()` is `null`, the same rest state
 * {@link injectHubRoutineTrendQuery} carries.
 */
export function injectHubRoutineSweepsQuery(routineId: () => string | null, since: () => string, until: () => string) {
  return injectQuery(() => {
    const id = routineId();
    return {
      queryKey: id === null ? hubRoutineSweepsKey('', '', '') : hubRoutineSweepsKey(id, since(), until()),
      enabled: id !== null,
      queryFn: async (): Promise<GardenSweepsView> => {
        const { data, error } = await routineSweepsApiRoutinesRoutineIdSweepsGet({
          path: { routine_id: id! },
          query: { since: since(), until: until() },
          throwOnError: false,
        });
        if (error) throw error;
        return data as GardenSweepsView;
      },
    };
  });
}
