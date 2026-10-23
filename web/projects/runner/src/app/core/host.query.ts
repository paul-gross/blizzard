import { computed, type Signal } from '@angular/core';
import { injectQuery } from '@tanstack/angular-query-experimental';
import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS, runnerApi } from 'fleet/runner-shell';

import {
  hostEnvironmentsKey,
  hostRegistrationKey,
  hostRunnersKey,
  hostSubscriptionsKey,
  hostWorkspacesKey,
} from './query-keys';
import { runnerNameResolver } from './host-pools.model';

/**
 * The host's reads — `GET /api/host/*`, at the host's root and through the generated
 * runner client (`bzh:generated-client`). Every live frame stales them all
 * (`runner-live-updates.ts`'s registry), so each interval is a backstop against a dropped
 * frame, the same stance as the runner's own dashboard read.
 */

/** `GET /api/host/runners` — every runner the host serves, and every one it holds halted. */
export async function fetchHostRunners(): Promise<runnerApi.HostRunnerView[]> {
  const { data, error } = await runnerApi.listRunnersApiHostRunnersGet({ throwOnError: false });
  if (error) throw error;
  return data?.items ?? [];
}

/** {@link fetchHostRunners} as a live query. */
export function injectHostRunnersQuery() {
  return injectQuery(() => ({
    queryKey: hostRunnersKey,
    queryFn: fetchHostRunners,
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

/** `GET /api/host/registration` — the host's registration at its hub, as its latest
 * report found it. */
export function injectHostRegistrationQuery() {
  return injectQuery(() => ({
    queryKey: hostRegistrationKey,
    queryFn: async (): Promise<runnerApi.HostRegistrationView> => {
      const { data, error } = await runnerApi.hostRegistrationApiHostRegistrationGet({ throwOnError: false });
      if (error) throw error;
      return data!;
    },
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/** {@link runnerNameResolver} over the host's live runner list. */
export function injectRunnerNames(): Signal<(runnerId: string) => string> {
  const runners = injectHostRunnersQuery();
  return computed(() => runnerNameResolver(runners.data() ?? []));
}
