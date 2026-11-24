import { computed, type Signal } from '@angular/core';
import { injectQuery, type QueryClient } from '@tanstack/angular-query-experimental';
import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS, runnerApi } from 'fleet/runner-shell';

import {
  hostEnvironmentsKey,
  hostRegistrationKey,
  hostRunnersKey,
  hostSubscriptionsKey,
  hostWorkspacesKey,
} from './query-keys';
import { runnerNameResolver } from './host-pools.model';
import { runnerSubscription } from './host-subscription.translate';
import type { HostRunner } from './host-runner.domain';
import { fromHostRunnerView } from './host-runner.translate';
import { fromHostWorkspaceEnvironmentsView } from './host-environment.translate';
import { fromHostRegistrationView } from './host-registration.translate';
import { fromHostWorkspaceView } from './host-workspace.translate';

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

/** {@link injectHostRunnersQuery} projected to {@link HostRunner} domains, over the same cache entry. */
export function injectHostRunnerDomainsQuery() {
  return injectQuery(() => ({
    queryKey: hostRunnersKey,
    queryFn: fetchHostRunners,
    select: (rows: runnerApi.HostRunnerView[]) => rows.map(fromHostRunnerView),
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/** The host's runners through `queryClient`'s cache, as {@link HostRunner} domains — the one-shot read a route
 * guard takes outside an injection-bound query. */
export async function fetchHostRunnerDomains(queryClient: QueryClient): Promise<readonly HostRunner[]> {
  const rows = await queryClient.fetchQuery({ queryKey: hostRunnersKey, queryFn: fetchHostRunners });
  return rows.map(fromHostRunnerView);
}

async function fetchHostWorkspaces(): Promise<runnerApi.HostWorkspaceView[]> {
  const { data, error } = await runnerApi.listWorkspacesApiHostWorkspacesGet({ throwOnError: false });
  if (error) throw error;
  return data?.items ?? [];
}

/** `GET /api/host/workspaces` — the workspaces the host declares, each with its capacity. */
export function injectHostWorkspacesQuery() {
  return injectQuery(() => ({
    queryKey: hostWorkspacesKey,
    queryFn: fetchHostWorkspaces,
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/** {@link injectHostWorkspacesQuery} projected to `HostWorkspace` domains, over the same cache entry. */
export function injectHostWorkspaceDomainsQuery() {
  return injectQuery(() => ({
    queryKey: hostWorkspacesKey,
    queryFn: fetchHostWorkspaces,
    select: (rows: runnerApi.HostWorkspaceView[]) => rows.map(fromHostWorkspaceView),
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

async function fetchHostEnvironments(): Promise<runnerApi.HostWorkspaceEnvironmentsView[]> {
  const { data, error } = await runnerApi.listEnvironmentsApiHostEnvironmentsGet({ throwOnError: false });
  if (error) throw error;
  return data?.workspaces ?? [];
}

/** `GET /api/host/environments` — the host's environments grouped by workspace, each held
 * one naming its runner and its chunk's project. */
export function injectHostEnvironmentsQuery() {
  return injectQuery(() => ({
    queryKey: hostEnvironmentsKey,
    queryFn: fetchHostEnvironments,
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/** {@link injectHostEnvironmentsQuery} projected to `WorkspaceEnvironments` domains, over the same cache
 * entry. */
export function injectHostEnvironmentDomainsQuery() {
  return injectQuery(() => ({
    queryKey: hostEnvironmentsKey,
    queryFn: fetchHostEnvironments,
    select: (rows: runnerApi.HostWorkspaceEnvironmentsView[]) => rows.map(fromHostWorkspaceEnvironmentsView),
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
    select: (rows: runnerApi.HostSubscriptionView[]) => rows.map(runnerSubscription),
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

async function fetchHostRegistration(): Promise<runnerApi.HostRegistrationView> {
  const { data, error } = await runnerApi.hostRegistrationApiHostRegistrationGet({ throwOnError: false });
  if (error) throw error;
  return data!;
}

/** `GET /api/host/registration` — the host's registration at its hub, as its latest
 * report found it. */
export function injectHostRegistrationQuery() {
  return injectQuery(() => ({
    queryKey: hostRegistrationKey,
    queryFn: fetchHostRegistration,
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/** {@link injectHostRegistrationQuery} projected to its `HostRegistration` domain, over the same cache entry. */
export function injectHostRegistrationDomainQuery() {
  return injectQuery(() => ({
    queryKey: hostRegistrationKey,
    queryFn: fetchHostRegistration,
    select: fromHostRegistrationView,
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/** {@link runnerNameResolver} over the host's live runner list. */
export function injectRunnerNames(): Signal<(runnerId: string) => string> {
  const runners = injectHostRunnerDomainsQuery();
  return computed(() => runnerNameResolver(runners.data() ?? []));
}
