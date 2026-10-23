import { type Signal, computed, inject } from '@angular/core';
import type { QueryObserverResult } from '@tanstack/angular-query-experimental';
import { injectQueries } from '@tanstack/angular-query-experimental/inject-queries-experimental';
import type { runnerApi } from 'fleet/runner-shell';

import { injectHostRunnersQuery } from './host.query';
import { runnerLeasesQueryOptions } from './leases.query';
import { PanelFilters } from './panel-filters';
import {
  inProject,
  attributedProject,
  mergeLeases,
  ownByProject,
  type PanelAsk,
  type PanelFact,
  type PanelLease,
  type PanelProject,
  projectCatalog,
  type RunnerDashboard,
  type RunnerOwner,
  type RunnerProjects,
  runnerProjects,
  scopedOwners,
  servingOwners,
} from './panel-reads.model';
import { runnerDashboardQueryOptions } from './status.query';

/**
 * The panel's reads across the host's runners. Every runner-scoped read fans out
 * client-side: one query per runner read, each under that runner's own key, so two
 * runners never share a cache entry and a live frame naming one stales only its own. The
 * results merge in computeds, every merged item carrying the runner it came from
 * ({@link RunnerOwner}) so its actions and links target that runner.
 *
 * A page reads every serving runner while the Runner filter is All, only the named one
 * otherwise ({@link PanelFilters}); the Project filter then narrows the merged items
 * client-side.
 */

/** A read merged across runners — `data` plus the async triad `asyncState` folds. Pending
 * while the host's runner list or any runner's read is; in error only once every runner's
 * read failed, so one unreachable runner never blanks the others. */
export interface MergedRead<T> {
  readonly data: Signal<T>;
  readonly isPending: Signal<boolean>;
  readonly isError: Signal<boolean>;
}

/** Which runners a read covers: those the Runner filter leaves, or every serving runner. */
export type RunnerScope = 'scoped' | 'all';

/** The host's serving runners as {@link RunnerOwner}s, and those the Runner filter leaves. */
export interface PanelRunners {
  readonly all: Signal<readonly RunnerOwner[]>;
  readonly scoped: Signal<readonly RunnerOwner[]>;
  readonly pending: Signal<boolean>;
}

/** The host's serving runners. */
export function injectPanelRunners(): PanelRunners {
  const filters = inject(PanelFilters);
  const runners = injectHostRunnersQuery();
  const all = computed(() => servingOwners(runners.data() ?? []));
  return {
    all,
    scoped: computed(() => scopedOwners(all(), filters.runner())),
    pending: computed(() => runners.isPending()),
  };
}

/** One runner's answer in a fan-out, tagged with the runner it answers for. */
interface Tagged<T> {
  readonly runnerId: string;
  readonly value: T;
}

/** One fan-out's state: each runner's answer by id, and whether any runner is still pending
 * or every runner failed. */
interface FanOut<T> {
  readonly byRunner: ReadonlyMap<string, T>;
  readonly anyPending: boolean;
  readonly allFailed: boolean;
}

/**
 * One query per runner in `owners`, built from `options` — each answer tagged with its
 * runner through the observer's own `select`, so the cache keeps the plain body every
 * other reader of the key shares, and a combined result never pairs an answer with the
 * wrong runner while the runner list changes under it.
 */
function injectFanOut<T>(
  owners: Signal<readonly RunnerOwner[]>,
  options: (runnerId: string) => { queryKey: readonly unknown[]; queryFn: () => Promise<T>; refetchInterval: number },
): Signal<FanOut<T>> {
  return injectQueries(() => ({
    queries: owners().map((owner) => ({
      ...options(owner.runnerId),
      select: (value: T): Tagged<T> => ({ runnerId: owner.runnerId, value }),
    })),
    // `combine` is handed the observers' plain results; the experimental typing names them
    // signal proxies, so the results are read as what they are.
    combine: (results) => {
      const plain = results as unknown as readonly QueryObserverResult<Tagged<T>>[];
      return {
        byRunner: new Map(plain.flatMap((result) => (result.data ? [[result.data.runnerId, result.data.value] as const] : []))),
        anyPending: plain.some((result) => result.isPending),
        allFailed: plain.length > 0 && plain.every((result) => result.isError),
      };
    },
  }));
}

/** `owners`' answers in `fanOut`, in the owners' order, each beside its runner. */
function answers<T>(owners: readonly RunnerOwner[], fanOut: FanOut<T>): { owner: RunnerOwner; value: T }[] {
  return owners.flatMap((owner) => {
    const value = fanOut.byRunner.get(owner.runnerId);
    return value === undefined ? [] : [{ owner, value }];
  });
}

/** Every covered runner's dashboard, each beside its runner. */
export function injectPanelDashboards(scope: RunnerScope = 'scoped'): MergedRead<readonly RunnerDashboard[]> {
  const runners = injectPanelRunners();
  const owners = scope === 'all' ? runners.all : runners.scoped;
  const fanOut = injectFanOut(owners, runnerDashboardQueryOptions);
  return {
    data: computed(() => answers(owners(), fanOut()).map(({ owner, value }) => ({ owner, dashboard: value }))),
    isPending: computed(() => runners.pending() || fanOut().anyPending),
    isError: computed(() => fanOut().allFailed),
  };
}

/** Each serving runner's projects by runner id, from its dashboard and its host bindings. */
export function injectRunnerProjects(): Signal<RunnerProjects> {
  const runners = injectHostRunnersQuery();
  const dashboards = injectPanelDashboards('all');
  return computed(() => {
    const hosted = runners.data() ?? [];
    const byId = new Map(dashboards.data().map((entry) => [entry.owner.runnerId, entry.dashboard] as const));
    return new Map(
      hosted.flatMap((runner) =>
        runner.runner_id === null ? [] : [[runner.runner_id, runnerProjects(byId.get(runner.runner_id), runner)] as const],
      ),
    );
  });
}

/** Every project the host serves, by name — the Project filter's choices. */
export function injectPanelProjects(): Signal<readonly PanelProject[]> {
  const byRunner = injectRunnerProjects();
  return computed(() => projectCatalog(byRunner()));
}

/** {@link injectPanelLeases}'s options. */
export interface PanelLeasesOptions {
  /** Which runners to read — every serving runner for `'all'`, ignoring the Runner filter. */
  readonly scope?: RunnerScope;
  /** Whether the Project filter narrows the leases; `false` keeps every project's. */
  readonly byProject?: boolean;
}

/** Every covered runner's leases as one list ({@link mergeLeases}), each lease attributed to
 * its project ({@link attributedProject}) and narrowed by the Project filter. */
export function injectPanelLeases(options: PanelLeasesOptions = {}): MergedRead<readonly PanelLease[]> {
  const filters = inject(PanelFilters);
  const runners = injectPanelRunners();
  const owners = options.scope === 'all' ? runners.all : runners.scoped;
  const fanOut = injectFanOut(owners, runnerLeasesQueryOptions);
  const projects = injectRunnerProjects();
  const all = computed(() => {
    const byRunner = projects();
    return mergeLeases(
      answers(owners(), fanOut()).map(({ owner, value }) => {
        const served = byRunner.get(owner.runnerId) ?? [];
        return value.map((lease) => ({ ...lease, owner, projectSlug: attributedProject(lease, served) }));
      }),
    );
  });
  return {
    data: computed(() => (options.byProject === false ? all() : inProject(all(), filters.project()))),
    isPending: computed(() => runners.pending() || fanOut().anyPending),
    isError: computed(() => fanOut().allFailed),
  };
}

/** A dashboard section's items merged across the covered runners, attributed by their own
 * project and narrowed by the Project filter. */
function injectDashboardItems<T extends { readonly project_id?: string | null }>(
  section: (dashboard: runnerApi.DashboardView) => readonly T[],
): MergedRead<readonly (T & { readonly owner: RunnerOwner; readonly projectSlug: string | null })[]> {
  const filters = inject(PanelFilters);
  const dashboards = injectPanelDashboards();
  const projects = injectRunnerProjects();
  return {
    data: computed(() => {
      const byRunner = projects();
      const items = dashboards
        .data()
        .flatMap(({ owner, dashboard }) => ownByProject(section(dashboard), owner, byRunner.get(owner.runnerId) ?? []));
      return inProject(items, filters.project());
    }),
    isPending: dashboards.isPending,
    isError: dashboards.isError,
  };
}

/** Every covered runner's newest facts, newest first. */
export function injectPanelFacts(): MergedRead<readonly PanelFact[]> {
  const items = injectDashboardItems((dashboard) => dashboard.facts?.items ?? []);
  return { ...items, data: computed(() => [...items.data()].sort((a, b) => b.created_at.localeCompare(a.created_at))) };
}

/** Every covered runner's open asks. */
export function injectPanelAsks(): MergedRead<readonly PanelAsk[]> {
  return injectDashboardItems((dashboard) => dashboard.asks?.items ?? []);
}

/** Every covered runner's open escalations. */
export function injectPanelEscalations(): MergedRead<readonly (runnerApi.EscalationView & { readonly owner: RunnerOwner })[]> {
  return injectDashboardItems((dashboard) => dashboard.escalations?.items ?? []);
}
