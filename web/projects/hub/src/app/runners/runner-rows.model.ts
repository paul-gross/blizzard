import {
  type ChunkStatus,
  type HostRegistryView,
  RunnerConnection,
  type RunnerRegistryView,
  type SubscriptionPace,
  compactRef,
  formatSeenAgo,
  hubSubscriptionPaces,
} from 'fleet';
import type { ChunkSummary } from 'fleet/shell';

import { isUnassigned, servedProjects, unresolvedEntries } from '../core/project-serving';
import { type BindingLine, bindingLines, subscriptionName } from './host-rows.model';

/** One claim line under a registry row: the chunk a runner holds, where it sits, and
 * how it is doing there — the node alone reads the same for a chunk actively
 * running and one parked `needs_human`. */
export interface ClaimLine {
  readonly chunkId: string;
  readonly shortId: string;
  readonly node: string;
  readonly status: ChunkStatus;
}

/**
 * A row's liveness label: a never-connected runner has no contact to age, so it says so;
 * otherwise "seen 12s ago" from `last_seen_at` (`bzh:utc-instants`). The hub decides the
 * connection (`connection`, `online`) on its own clock; this label is decoration read against
 * the browser's, so it defers to the shared skew-tolerant `formatSeenAgo` (`when.ts`).
 */
export function runnerSeenLabel(row: RunnerRow): string {
  if (row.connection === RunnerConnection.NEVER_CONNECTED) return 'never connected';
  return formatSeenAgo(row.last_seen_at, row.online, row.nowMs);
}

/** A registry row: the runner plus its claims and subscription pace groups, pre-folded
 * so a presentational view needs no second read to render them. `used` is the slot
 * bar's numerator — environments held by this runner's live routes. */
export interface RunnerRow extends RunnerRegistryView {
  readonly claims: readonly ClaimLine[];
  readonly used: number;
  readonly subscriptionPaces: readonly SubscriptionPace[];
  /** The display name of each project the runner serves, in its declaration's order; an entry naming no project of the
   * tenant is in {@link unresolved} instead. */
  readonly served: readonly string[];
  /** Whether the runner declares no project at all — it is offered no work, so an idle row reads as unassigned rather
   * than as starved. */
  readonly unassigned: boolean;
  /** The entries of the runner's declaration that name no project of the tenant, as the runner wrote them. */
  readonly unresolved: readonly string[];
  /** Each binding the runner declares under its host, with its agents in use; empty for a host-less runner. */
  readonly bindingLines: readonly BindingLine[];
  /** The display name of the runner's one subscription, from its host's roster (the slug where the roster names
   * none); `null` when it names no subscription. */
  readonly subscriptionName: string | null;
  /** The registry's one clock reading (`injectNowSignal`), so every time-relative label on
   * the row — pace bars, "seen Ns ago" — ticks together rather than each reading `Date.now()`. */
  readonly nowMs: number;
}

/** Why the runner stopped itself: a spend-ceiling crossing names the ceiling and the
 * spend it reported (`locally_paused_reason`); a manual pause carries none, so this
 * falls back to a generic clear-it-yourself hint. */
export function localPauseHint(row: RunnerRow): string {
  return row.locally_paused_reason ?? 'This runner paused itself. Clear it on the runner: blizzard runner start';
}

/** Why resuming at the hub may not start a runner: its own brake is not the hub's to
 * clear. */
export function runnerToggleHint(row: RunnerRow): string {
  if (row.hub_paused && row.locally_paused) {
    return 'Resuming here clears the hub brake only — this runner also paused itself.';
  }
  return row.hub_paused ? 'Resume this runner at the hub' : 'Pause this runner at the hub';
}

/** Every routed chunk grouped by the runner holding it — each as a claim line
 * (short name + current node + status) for the registry rows.
 *
 * No status filter by design: `ChunkSummary.runner_id` is already in-progress-only
 * (see its own docs), so `runner_id` set *is* "currently holds a route on". */
function claimsByRunner(chunks: readonly ChunkSummary[]): Map<string, ClaimLine[]> {
  const grouped = new Map<string, ClaimLine[]>();
  for (const chunk of chunks) {
    if (!chunk.runner_id) continue;
    const lines = grouped.get(chunk.runner_id) ?? [];
    lines.push({
      chunkId: chunk.chunk_id,
      shortId: compactRef(chunk.chunk_id),
      node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
      status: chunk.status,
    });
    grouped.set(chunk.runner_id, lines);
  }
  return grouped;
}

/** Environments held per runner — the slot bar's numerator, summed from
 * each of its chunks' `environment_count`. A grouped chunk holding >1 environment
 * counts them all, so a runner working one 3-env chunk reads as using 3 slots, not 1.
 * Environments are exclusively leased and a runner's chunks are distinct, so a plain
 * sum needs no dedup. Unfiltered by status for the same reason {@link claimsByRunner} is. */
function usedByRunner(chunks: readonly ChunkSummary[]): Map<string, number> {
  const used = new Map<string, number>();
  for (const chunk of chunks) {
    if (!chunk.runner_id) continue;
    used.set(chunk.runner_id, (used.get(chunk.runner_id) ?? 0) + (chunk.environment_count ?? 0));
  }
  return used;
}

/** Each runner with its claims, slot-bar numerator, subscription pace groups, and bindings
 * folded on, every time-relative field read against the one `nowMs`; `hosts` is the roster
 * each runner's subscription name resolves from. */
export function foldRunnerRows(
  runners: readonly RunnerRegistryView[],
  chunks: readonly ChunkSummary[],
  nowMs: number,
  hosts: readonly HostRegistryView[] = [],
): readonly RunnerRow[] {
  const claims = claimsByRunner(chunks);
  const used = usedByRunner(chunks);
  const hostsById = new Map(hosts.map((host) => [host.host_id, host]));
  return runners.map((runner) => ({
    ...runner,
    claims: claims.get(runner.runner_id) ?? [],
    used: used.get(runner.runner_id) ?? 0,
    subscriptionPaces: hubSubscriptionPaces(runner.subscriptions ?? [], nowMs),
    served: servedNames(runner),
    unassigned: isUnassigned(runner),
    unresolved: unresolvedEntries(runner),
    bindingLines: bindingLines(runner, chunks),
    subscriptionName: subscriptionName(runner, hostsById),
    nowMs,
  }));
}

/** The display name of each resolved project in `runner`'s declaration — its name, else its slug. */
function servedNames(runner: RunnerRegistryView): readonly string[] {
  return servedProjects(runner)
    .filter((served) => served.project_id != null)
    .map((served) => served.name ?? served.slug ?? served.entry);
}

/**
 * The runners a registry under the project lens shows: on All (`null`) every runner; under a lens the runners whose
 * declaration resolves to the lensed project (matched by its current slug, which the lens holds), and the unassigned
 * ones, which serve no project and so wait on every project alike.
 */
export function lensRunners<Runner extends RunnerRegistryView>(runners: readonly Runner[], slug: string | null): readonly Runner[] {
  if (slug === null) return runners;
  return runners.filter(
    (runner) =>
      isUnassigned(runner) || servedProjects(runner).some((served) => served.project_id != null && served.slug === slug),
  );
}
