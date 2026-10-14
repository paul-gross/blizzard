import { computed, type Signal } from '@angular/core';
import { injectQuery } from '@tanstack/angular-query-experimental';
import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS, runnerApi } from 'fleet';

import { hostEnvironmentsKey, hostRunnersKey, hostSubscriptionsKey, hostWorkspacesKey } from '../core/query-keys';

/**
 * The host's reads — `GET /api/host/*`, at the host's root and through the generated
 * runner client (`bzh:generated-client`). Every live frame stales them all
 * (`runner-live-updates.ts`'s registry), so each interval is a backstop against a dropped
 * frame, the same stance as the runner's own dashboard read.
 */

/** `GET /api/host/runners` — every runner the host serves, and every one it holds halted. */
export function injectHostRunnersQuery() {
  return injectQuery(() => ({
    queryKey: hostRunnersKey,
    queryFn: async (): Promise<runnerApi.HostRunnerView[]> => {
      const { data, error } = await runnerApi.listRunnersApiHostRunnersGet({ throwOnError: false });
      if (error) throw error;
      return data?.items ?? [];
    },
    // See RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/** `GET /api/host/workspaces` — the workspaces the host declares, each with its capacity. */
export function injectHostWorkspacesQuery() {
  return injectQuery(() => ({
    queryKey: hostWorkspacesKey,
    queryFn: async (): Promise<runnerApi.HostWorkspaceView[]> => {
      const { data, error } = await runnerApi.listWorkspacesApiHostWorkspacesGet({ throwOnError: false });
      if (error) throw error;
      return data?.items ?? [];
    },
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/** `GET /api/host/environments` — the host's environments grouped by workspace, each held
 * one naming its runner and its chunk's project. */
export function injectHostEnvironmentsQuery() {
  return injectQuery(() => ({
    queryKey: hostEnvironmentsKey,
    queryFn: async (): Promise<runnerApi.HostWorkspaceEnvironmentsView[]> => {
      const { data, error } = await runnerApi.listEnvironmentsApiHostEnvironmentsGet({ throwOnError: false });
      if (error) throw error;
      return data?.workspaces ?? [];
    },
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/** `GET /api/host/subscriptions` — each declared subscription's newest sampling attempt,
 * naming the runners that spend it. */
export function injectHostSubscriptionsQuery() {
  return injectQuery(() => ({
    queryKey: hostSubscriptionsKey,
    queryFn: async (): Promise<runnerApi.HostSubscriptionView[]> => {
      const { data, error } = await runnerApi.listSubscriptionsApiHostSubscriptionsGet({ throwOnError: false });
      if (error) throw error;
      return data?.items ?? [];
    },
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/** A runner id's display name off the host's runner list — the id itself for one the list
 * does not (yet) name. */
export function runnerNameResolver(runners: readonly runnerApi.HostRunnerView[]): (runnerId: string) => string {
  const names = new Map(runners.flatMap((runner) => (runner.runner_id === null ? [] : [[runner.runner_id, runner.name] as const])));
  return (runnerId) => names.get(runnerId) ?? runnerId;
}

/** {@link runnerNameResolver} over the host's live runner list. */
export function injectRunnerNames(): Signal<(runnerId: string) => string> {
  const runners = injectHostRunnersQuery();
  return computed(() => runnerNameResolver(runners.data() ?? []));
}
