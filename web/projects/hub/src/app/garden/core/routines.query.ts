import { injectQuery } from '@tanstack/angular-query-experimental';

import { injectProjectFilter, lensedKey } from 'fleet/shell';
import { listProjectRoutinesApiProjectsProjectRoutinesGet, listRoutinesApiRoutinesGet, listRoutineScopesApiRoutinesRoutineIdScopesGet, routineProposalCountsApiRoutinesProposalCountsGet, routineSweepsApiRoutinesRoutineIdSweepsGet, routineTrendApiRoutinesTrendGet, type GardenProposalCountsView, type GardenSweepsView, type RoutineView, type TrendView, hubRoutineProposalCountsKey, hubRoutineScopesKey, hubRoutineSweepsKey, hubRoutineTrendKey, hubRoutinesKey } from 'fleet';

/**
 * The routine list — every routine, newest first, including a retired one marked as such. Under a project lens it is
 * that project's routines (`GET /api/projects/{project}/routines`); on All it is the tenant's (`GET /api/routines`),
 * which only the gardening doorway reads, grouping them by project: every consumer of this one query renders a routine's
 * full history regardless of its lifecycle state. Routines change rarely and carry no
 * SSE event of their own, `injectHubGraphsQuery`'s own standing.
 */
export function injectHubRoutinesQuery() {
  const project = injectProjectFilter();
  return injectQuery(() => {
    const lens = project();
    return {
      queryKey: lensedKey(hubRoutinesKey, lens),
      queryFn: async (): Promise<RoutineView[]> => {
        const { data, error } =
          lens === null
            ? await listRoutinesApiRoutinesGet({ query: { include_retired: true }, throwOnError: false })
            : await listProjectRoutinesApiProjectsProjectRoutinesGet({
                path: { project: lens },
                query: { include_retired: true },
                throwOnError: false,
              });
        if (error) throw error;
        return data ?? [];
      },
    };
  });
}

/**
 * Hub `GET /api/routines/trend` read — one routine's finding inflow against outflow
 * over `[since, until)`. Every argument is an accessor so the caller can recompute the
 * window without re-wiring the query (`injectHubFleetSpendQuery`'s own shape). A routine's name is unique only inside
 * its project, so under a lens the read names it (`?project=`); disabled while `routineName()` is `null` — the
 * panel's own "nothing selected" rest state.
 */
export function injectHubRoutineTrendQuery(
  routineName: () => string | null,
  since: () => string,
  until: () => string,
  introducedBoundary: () => string,
  periodDays: () => number,
) {
  const project = injectProjectFilter();
  return injectQuery(() => {
    const name = routineName();
    const lens = project();
    return {
      queryKey: lensedKey(hubRoutineTrendKey(name, since(), until(), introducedBoundary(), periodDays()), lens),
      enabled: name !== null,
      queryFn: async (): Promise<TrendView> => {
        const { data, error } = await routineTrendApiRoutinesTrendGet({
          query: {
            routine: name!,
            since: since(),
            until: until(),
            introduced_boundary: introducedBoundary(),
            period_days: periodDays(),
            ...(lens !== null ? { project: lens } : {}),
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
 * last-swept table (unwindowed) and its measurement series over `[since, until)`.
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
 * Hub `GET /api/routines/proposal-counts` read — one routine's
 * garden-proposal counts, per class, over `[since, until)`. Every window argument is
 * an accessor, `injectHubRoutineTrendQuery`'s own shape, and like it the read names the lensed project; disabled while `routineName()` is `null`, the same "nothing selected" rest state.
 */
export function injectHubRoutineProposalCountsQuery(
  routineName: () => string | null,
  since: () => string,
  until: () => string,
) {
  const project = injectProjectFilter();
  return injectQuery(() => {
    const name = routineName();
    const lens = project();
    return {
      queryKey: lensedKey(hubRoutineProposalCountsKey(name, since(), until()), lens),
      enabled: name !== null,
      queryFn: async (): Promise<GardenProposalCountsView> => {
        const { data, error } = await routineProposalCountsApiRoutinesProposalCountsGet({
          query: { routine: name!, since: since(), until: until(), ...(lens !== null ? { project: lens } : {}) },
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
