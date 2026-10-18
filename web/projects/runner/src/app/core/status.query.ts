import { inject } from '@angular/core';
import { QueryClient, injectMutation, injectQuery } from '@tanstack/angular-query-experimental';
import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS, runnerApi } from 'fleet';

import { injectActiveRunner } from './active-runner';
import { localPauseMutationKey } from './mutation-keys';
import { hostKey, runnerDashboardKey } from './query-keys';

/**
 * The active runner's whole status read, composed into one poll —
 * `GET /runners/{runner_id}/api/dashboard`, through the generated runner client
 * (`bzh:generated-client`); disabled on a host page, where no runner is active. Most of
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
 * client-visible error for that case). Every consumer injects this same query
 * directly rather than threading it down as an input — TanStack's own
 * query-key dedupe folds N injections of {@link runnerDashboardKey} into one
 * network request, so this is still one poll for the whole panel, not seven.
 */
export function injectRunnerDashboardQuery() {
  const runnerId = injectActiveRunner();
  return injectQuery(() => {
    const id = runnerId();
    return {
      queryKey: runnerDashboardKey(id),
      enabled: id !== null,
      queryFn: async (): Promise<runnerApi.DashboardView> => {
        const { data, error } = await runnerApi.getDashboardRunnersRunnerIdApiDashboardGet({
          path: { runner_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
      // See RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS.
      refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
    };
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
 * the panel's pause state comes from — and the host's reads, whose runner rows and switcher show the brake.
 */
export function injectLocalPauseMutation() {
  const queryClient = inject(QueryClient);
  const runnerId = injectActiveRunner();
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
