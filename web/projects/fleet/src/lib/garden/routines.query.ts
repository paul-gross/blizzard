import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  listRoutinesApiRoutinesGet,
  listRoutineScopesApiRoutinesRoutineIdScopesGet,
  routineProposalCountsApiRoutinesProposalCountsGet,
  routineSweepsApiRoutinesRoutineIdSweepsGet,
  routineTrendApiRoutinesTrendGet,
  type GardenProposalCountsView,
  type GardenSweepsView,
  type RoutineView,
  type TrendView,
} from '../api/hub';
import {
  hubRoutineProposalCountsKey,
  hubRoutineScopesKey,
  hubRoutineSweepsKey,
  hubRoutineTrendKey,
  hubRoutinesKey,
} from '../query-keys';

/**
 * Hub `GET /api/routines` read — every routine, newest first, including a retired one
 * marked as such: every consumer of this one query renders a routine's
 * full history regardless of its lifecycle state. Routines change rarely and carry no
 * SSE event of their own, `injectHubGraphsQuery`'s own standing.
 */
export function injectHubRoutinesQuery() {
  return injectQuery(() => ({
    queryKey: hubRoutinesKey,
    queryFn: async (): Promise<RoutineView[]> => {
      const { data, error } = await listRoutinesApiRoutinesGet({
        query: { include_retired: true },
        throwOnError: false,
      });
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
      queryKey: hubRoutineTrendKey(name, since(), until(), introducedBoundary(), periodDays()),
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
      queryKey: hubRoutineSweepsKey(id, since(), until()),
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

/**
 * Hub `GET /api/routines/proposal-counts` read (blizzard#547) — one routine's
 * garden-proposal counts, per class, over `[since, until)`. Every window argument is
 * an accessor, `injectHubRoutineTrendQuery`'s own shape; disabled while
 * `routineName()` is `null`, the same "nothing selected" rest state.
 */
export function injectHubRoutineProposalCountsQuery(
  routineName: () => string | null,
  since: () => string,
  until: () => string,
) {
  return injectQuery(() => {
    const name = routineName();
    return {
      queryKey: hubRoutineProposalCountsKey(name, since(), until()),
      enabled: name !== null,
      queryFn: async (): Promise<GardenProposalCountsView> => {
        const { data, error } = await routineProposalCountsApiRoutinesProposalCountsGet({
          query: { routine: name!, since: since(), until: until() },
          throwOnError: false,
        });
        if (error) throw error;
        return data as GardenProposalCountsView;
      },
    };
  });
}

/**
 * Hub `GET /api/routines/{routine_id}/scopes` read — every scope slug linked to a
 * routine's own set, its own default always among them. Disabled while `routineId()`
 * is `null`, the same rest state {@link injectHubRoutineSweepsQuery} carries.
 */
export function injectHubRoutineScopesQuery(routineId: () => string | null) {
  return injectQuery(() => {
    const id = routineId();
    return {
      queryKey: hubRoutineScopesKey(id),
      enabled: id !== null,
      queryFn: async (): Promise<string[]> => {
        const { data, error } = await listRoutineScopesApiRoutinesRoutineIdScopesGet({
          path: { routine_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data ?? [];
      },
    };
  });
}
