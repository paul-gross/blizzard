import { inject } from '@angular/core';
import { QueryClient, injectMutation, injectQuery } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { runnerDashboardKey } from './query-keys';

/**
 * The panel's whole machine-local status read, composed into one poll
 * (issue #311) — `GET /api/dashboard`, through the generated runner client
 * (`bzh:generated-client`), on the same 5s poll floor as `leases.query.ts`
 * — the runner has no event stream, so the poll is the only signal. Nests
 * every section the panel's rails read: `runner` (identity, capacities, hub
 * connectivity, last tick), `environments`, `asks`, `escalations`,
 * `takeovers`, `facts`, and `fleet_summary` (the one section that can be
 * `null` — a hub outage or an unwired runner, never a client-visible error
 * for that case). Every consumer injects this same query directly rather
 * than threading it down as an input — TanStack's own query-key dedupe
 * folds N injections of {@link runnerDashboardKey} into one network request,
 * so this is still one poll for the whole panel, not seven.
 */
export function injectRunnerDashboardQuery() {
  return injectQuery(() => ({
    queryKey: runnerDashboardKey,
    queryFn: async (): Promise<runnerApi.DashboardView> => {
      const { data, error } = await runnerApi.getDashboardApiDashboardGet({ throwOnError: false });
      if (error) throw error;
      return data!;
    },
    refetchInterval: 5000,
  }));
}

/**
 * `PATCH /api/runner` (issue #133) — the local pause brake's only mutation
 * surface in the web UI, through the generated runner client
 * (`bzh:generated-client`). Sets `paused` declaratively on the runner
 * singleton; the hub's own brake (`hub_paused`) is untouched — this route
 * neither reads nor writes it. Named `injectLocalPauseMutation` — not
 * `injectRunnerPauseMutation` — because `fleet`'s own `runners.mutations.ts`
 * already owns that name for the hub pausing *a* runner; this one is a
 * runner pausing *itself* (`bzh:frontend-selector-prefix` / issue #83's
 * collision class).
 *
 * On success, `onSuccess` **returns** the invalidation, not fires it
 * fire-and-forget: `injectMutation`'s `isPending()` only clears once the
 * returned promise settles, so the toggle stays disabled through the
 * re-read that follows the PATCH rather than re-enabling the instant the
 * PATCH itself resolves — closing the stale-read window where a fast second
 * click would compute its flip off the pre-PATCH `local` value and send the
 * opposite of what the operator just asked for. Invalidates
 * {@link runnerDashboardKey} — the panel now reads pause state off the
 * dashboard read, so invalidating the old per-endpoint key would be a
 * silent no-op and reopen that same stale-toggle race.
 */
export function injectLocalPauseMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (paused: boolean): Promise<runnerApi.RunnerControlView> => {
      const { data, error } = await runnerApi.patchRunnerApiRunnerPatch({ body: { paused }, throwOnError: false });
      if (error) throw error;
      return data!;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: runnerDashboardKey }),
  }));
}
