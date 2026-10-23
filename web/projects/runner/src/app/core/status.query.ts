import { inject } from '@angular/core';
import { QueryClient, injectMutation, injectQuery } from '@tanstack/angular-query-experimental';
import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS, runnerApi } from 'fleet';

import { floorClearMutationKey, localPauseMutationKey } from './mutation-keys';
import { hostKey, runnerDashboardKey } from './query-keys';

/**
 * One runner's whole status read, composed into one poll —
 * `GET /runners/{runner_id}/api/dashboard`, through the generated runner client
 * (`bzh:generated-client`). Most of
 * its sections (`runner`, `asks`, `escalations`, `takeovers`, `facts`) are largely
 * covered by the runner's own SSE stream — every
 * event kind `RunnerLiveUpdates` dispatches stales this read
 * (`runner-live-updates.ts`'s registry), so the interval below is mostly a
 * backstop against a dropped frame, not the primary freshness path. Two facts
 * inside `runner` are the exception: the daemon's own tick beat and its
 * hub-pause mirror carry no event kind at all (`polling.ts`), so for
 * those two this interval is the only refresh path, not a backstop.
 * `fleet_summary`, the seventh section, is a hub pass-through no runner event
 * can prove, and rides the same backstop for a different reason. Nests
 * every section the panel's rails read: `runner`
 * (identity, capacities, hub connectivity, last tick), `asks`,
 * `escalations`, `takeovers`, `facts`, `harness_health`, and `fleet_summary` (the one section
 * that can be `null` — a hub outage or an unwired runner, never a
 * client-visible error for that case). TanStack's query-key dedupe folds every
 * injection of one runner's {@link runnerDashboardKey} — the single-runner
 * {@link injectRunnerDashboardQuery} and the panel's fan-out (`panel-reads.ts`)
 * alike — into one network request.
 */
export function runnerDashboardQueryOptions(runnerId: string) {
  return {
    queryKey: runnerDashboardKey(runnerId),
    queryFn: async (): Promise<runnerApi.DashboardView> => {
      const { data, error } = await runnerApi.getDashboardRunnersRunnerIdApiDashboardGet({
        path: { runner_id: runnerId },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    // See RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  };
}

/** One runner's dashboard ({@link runnerDashboardQueryOptions}) — for a section that shows
 * a single runner; held while `runnerId` gives `null`. */
export function injectRunnerDashboardQuery(runnerId: () => string | null) {
  return injectQuery(() => {
    const id = runnerId();
    return { ...runnerDashboardQueryOptions(id ?? ''), enabled: id !== null };
  });
}

/** `injectLocalPauseMutation`'s variables — `paused` is the PATCH payload; `runnerId`
 * addresses the runner pausing itself, as its route's `runner_id`. */
export interface LocalPauseVars {
  readonly runnerId: string;
  readonly paused: boolean;
}

/**
 * `PATCH /runners/{runner_id}/api/runner` — the local pause brake's only mutation
 * surface in the web UI, through the generated runner client
 * (`bzh:generated-client`). Sets `paused` declaratively on the runner
 * singleton; the hub's own brake (`hub_paused`) is untouched — this route
 * neither reads nor writes it.
 *
 * `onSettled` returns the invalidation, so `isPending()` holds through the re-read.
 * Invalidates the paused runner's {@link runnerDashboardKey} — the read
 * the panel's pause state comes from — and the host's reads, whose runner rows and Runner filter show the brake.
 */
export function injectLocalPauseMutation(runnerId: () => string) {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: localPauseMutationKey(runnerId()),
    mutationFn: async (vars: LocalPauseVars): Promise<runnerApi.RunnerControlView> => {
      const { data, error } = await runnerApi.patchRunnerRunnersRunnerIdApiRunnerPatch({
        path: { runner_id: vars.runnerId },
        body: { paused: vars.paused },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: runnerDashboardKey(vars.runnerId) }),
        queryClient.invalidateQueries({ queryKey: hostKey }),
      ]),
  }));
}

export interface FloorClearVars {
  readonly runnerId: string;
  readonly subscription: string | null;
}

export function injectFloorClearMutation(runnerId: () => string) {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: floorClearMutationKey(runnerId()),
    mutationFn: async ({ runnerId: id, subscription }: FloorClearVars) => {
      const result = subscription === null
        ? await runnerApi.clearRunnerFloorRunnersRunnerIdApiRunnerFloorClearPost({
            path: { runner_id: id }, body: {}, throwOnError: false,
          })
        : await runnerApi.clearSubscriptionFloorApiHostSubscriptionsSlugFloorClearPost({
            path: { slug: subscription }, body: {}, throwOnError: false,
          });
      if (result.error) throw result.error;
      return result.data;
    },
    onSettled: (_data, _error, vars) => Promise.all([
      queryClient.invalidateQueries({ queryKey: hostKey }),
      queryClient.invalidateQueries(vars.subscription === null
        ? { queryKey: runnerDashboardKey(vars.runnerId) }
        : { predicate: (query) => query.queryKey[1] === 'dashboard' }),
    ]),
  }));
}
