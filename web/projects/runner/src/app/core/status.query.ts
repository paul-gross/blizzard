import { inject } from '@angular/core';
import { QueryClient, injectMutation, injectQuery } from '@tanstack/angular-query-experimental';
import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS, runnerApi } from 'fleet';

import { localPauseMutationKey } from './mutation-keys';
import { runnerDashboardKey } from './query-keys';

/**
 * The panel's whole machine-local status read, composed into one poll —
 * `GET /api/dashboard`, through the generated runner client
 * (`bzh:generated-client`). Six of its seven sections (`runner`,
 * `environments`, `asks`, `escalations`, `takeovers`, `facts`) are largely
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
 * (identity, capacities, hub connectivity, last tick), `environments`, `asks`,
 * `escalations`, `takeovers`, `facts`, and `fleet_summary` (the one section
 * that can be `null` — a hub outage or an unwired runner, never a
 * client-visible error for that case). Every consumer injects this same query
 * directly rather than threading it down as an input — TanStack's own
 * query-key dedupe folds N injections of {@link runnerDashboardKey} into one
 * network request, so this is still one poll for the whole panel, not seven.
 */
export function injectRunnerDashboardQuery() {
  return injectQuery(() => ({
    queryKey: runnerDashboardKey,
    queryFn: async (): Promise<runnerApi.DashboardView> => {
      const { data, error } = await runnerApi.getDashboardApiDashboardGet({ throwOnError: false });
      if (error) throw error;
      return data!;
    },
    // See RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/** `injectLocalPauseMutation`'s variables — `paused` is the PATCH payload; `runnerId`
 * identifies the runner pausing itself and is never sent on the wire. */
export interface LocalPauseVars {
  readonly runnerId: string;
  readonly paused: boolean;
}

/**
 * `PATCH /api/runner` — the local pause brake's only mutation
 * surface in the web UI, through the generated runner client
 * (`bzh:generated-client`). Sets `paused` declaratively on the runner
 * singleton; the hub's own brake (`hub_paused`) is untouched — this route
 * neither reads nor writes it. Named `injectLocalPauseMutation` — not
 * `injectRunnerPauseMutation` — because `fleet`'s own `runners.mutations.ts`
 * already owns that name for the hub pausing *a* runner; this one is a
 * runner pausing *itself* (`bzh:frontend-selector-prefix`'s
 * collision class).
 *
 * `onSettled` **returns** the invalidation, not fires it fire-and-forget:
 * `injectMutation`'s `isPending()` only clears once the returned promise settles, so
 * the toggle stays disabled through the re-read that follows the PATCH rather than
 * re-enabling the instant the PATCH itself resolves — closing the stale-read window
 * where a fast second click would compute its flip off the pre-PATCH `local` value
 * and send the opposite of what the operator just asked for. `onSettled` rather than
 * `onSuccess` so pending survives a failed invalidation attempt too, not just a
 * successful one. Invalidates {@link runnerDashboardKey} — the panel now reads pause
 * state off the dashboard read, so invalidating the old per-endpoint key would be a
 * silent no-op and reopen that same stale-toggle race.
 */
export function injectLocalPauseMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: localPauseMutationKey,
    mutationFn: async (vars: LocalPauseVars): Promise<runnerApi.RunnerControlView> => {
      const { data, error } = await runnerApi.patchRunnerApiRunnerPatch({
        body: { paused: vars.paused },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: runnerDashboardKey }),
  }));
}
