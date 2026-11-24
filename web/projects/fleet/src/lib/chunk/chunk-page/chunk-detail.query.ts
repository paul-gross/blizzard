import { injectQuery } from '@tanstack/angular-query-experimental';

import * as hubApi from '../../api/hub';
import type { ChunkDetail as WireChunkDetail, WorkItemsView } from '../../api/hub';
import { client as hubClient } from '../../api/hub/client.gen';
import * as runnerApi from '../../api/runner';
import { LIVE_COVERED_POLL_BACKSTOP_MS, RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS } from '../../core/polling';
import { type DaemonTarget, targetKeyRoot } from '../../core/daemon-target/daemon-target';
import { chunkDetailKey, chunkWorkItemsKey, type TranscriptPlane } from '../../core/query-keys';
import { laneStatus } from '../../core/chunk-lanes.translate';
import { fromArtifactView } from '../chunk-detail/artifact.translate';
import { fromWorkItemsView } from './work-items.translate';
import { toAwaitingHuman } from '../awaiting-human/awaiting-human.translate';
import type { ChunkDetail } from './chunk-detail.model';
import { fromChunkDeleteRefusal, fromChunkWaitKind, fromMigrationMode, fromMigrationSource, fromUsageKind, fromBounceView, fromChunkFlowPlaceView, fromFlowFactView } from './chunk-wait.translate';

/** Each plane's live-covered backstop — the interval its own live-update registry
 * leaves as insurance against a dropped frame. */
const POLL_BACKSTOP_MS: Record<TranscriptPlane, number> = {
  hub: LIVE_COVERED_POLL_BACKSTOP_MS,
  runner: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
};

/** Project presentation statuses without changing the cached aggregate. */
export function projectChunkDetail(detail: WireChunkDetail): ChunkDetail {
  const project = (neighbor: hubApi.ChunkNeighborView) => ({
    chunk_id: neighbor.chunk_id,
    satisfied: neighbor.satisfied,
    laneStatus: neighbor.status === null ? null : laneStatus(neighbor.status),
  });
  return {
    artifacts: detail.artifacts.map(fromArtifactView),
    awaiting_external_merge: detail.awaiting_external_merge,
    blocked: detail.blocked && { prerequisite_chunk_id: detail.blocked.prerequisite_chunk_id, unmet_count: detail.blocked.unmet_count },
    bounces: detail.bounces.map(fromBounceView),
    chunk_id: detail.chunk_id,
    closed_prs: detail.closed_prs.map((p) => ({ number: p.number, repo: p.repo, url: p.url })),
    completable: detail.completable,
    cost: { billed_partial: detail.cost.billed_partial, cache_create_tokens: detail.cost.cache_create_tokens,
      cache_read_tokens: detail.cost.cache_read_tokens, cost_partial: detail.cost.cost_partial, cost_usd: detail.cost.cost_usd,
      estimated_cost_usd: detail.cost.estimated_cost_usd, input_tokens: detail.cost.input_tokens, output_tokens: detail.cost.output_tokens },
    current_node_id: detail.current_node_id,
    current_node_name: detail.current_node_name,
    current_node_terminal: detail.current_node_terminal,
    default_effort: detail.default_effort,
    default_harnesses: [...detail.default_harnesses],
    default_model: [...detail.default_model],
    deletable: detail.deletable,
    delete_refusal: detail.delete_refusal === null ? null : fromChunkDeleteRefusal(detail.delete_refusal),
    escalation: detail.escalation === null ? null : {
      cause: detail.escalation.cause,
      detail: detail.escalation.detail,
      epoch: detail.escalation.epoch,
      takeover_command: detail.escalation.takeover_command,
      wrapped_takeover_command: detail.escalation.wrapped_takeover_command,
    },
    flow_places: detail.flow_places.map(fromChunkFlowPlaceView),
    flow_record: detail.flow_record.map(fromFlowFactView),
    graph_created_at: detail.graph_created_at,
    graph_editable: detail.graph_editable,
    graph_id: detail.graph_id,
    graph_name: detail.graph_name,
    history: detail.history.map((h) => ({ choice_name: h.choice_name, epoch: h.epoch, from_node_id: h.from_node_id,
      from_node_name: h.from_node_name, graph_id: h.graph_id, graph_name: h.graph_name, recorded_at: h.recorded_at,
      to_node_id: h.to_node_id, to_node_name: h.to_node_name })),
    intended_migration: detail.intended_migration && { graph_id: detail.intended_migration.graph_id, graph_name: detail.intended_migration.graph_name,
      mode: fromMigrationMode(detail.intended_migration.mode), node_name: detail.intended_migration.node_name },
    landed: detail.landed,
    landed_repos: detail.landed_repos.map((r) => ({ commit_hash: r.commit_hash, repo: r.repo, url: r.url })),
    latest_epoch: detail.latest_epoch,
    migrations: detail.migrations.map((m) => ({ choice_name: m.choice_name, epoch: m.epoch, from_graph_id: m.from_graph_id,
      from_graph_name: m.from_graph_name, from_node_id: m.from_node_id, from_node_name: m.from_node_name,
      landed_node_id: m.landed_node_id, landed_node_name: m.landed_node_name, model: m.model, recorded_at: m.recorded_at,
      source: m.source === null ? null : fromMigrationSource(m.source), to_graph_id: m.to_graph_id, to_graph_name: m.to_graph_name })),
    open_prs: detail.open_prs.map((p) => ({ number: p.number, repo: p.repo, url: p.url })),
    pausable: detail.pausable,
    pause: detail.pause && { by: detail.pause.by, set_at: detail.pause.set_at },
    pending: detail.pending && { kind: fromChunkWaitKind(detail.pending.kind), next_poll_at: detail.pending.next_poll_at, node_name: detail.pending.node_name, since: detail.pending.since },
    project_id: detail.project_id,
    project_name: detail.project_name,
    project_slug: detail.project_slug,
    promotable: detail.promotable,
    repository_targets: detail.repository_targets.map((r) => ({ base_branch: r.base_branch, captured_at: r.captured_at,
      inherited: r.inherited, repository_id: r.repository_id, repository_slug: r.repository_slug })),
    requeues: detail.requeues.map((r) => ({ note: r.note, requeued_at: r.requeued_at, requeued_by: r.requeued_by })),
    resolutions: detail.resolutions.map((r) => ({ choice: r.choice, decision_id: r.decision_id, epoch: r.epoch, node_id: r.node_id,
      node_name: r.node_name, note: r.note, overrode_red_checks: r.overrode_red_checks, resolved_at: r.resolved_at, resolved_by: r.resolved_by })),
    restarts: detail.restarts.map((r) => ({ decision_id: r.decision_id, epoch: r.epoch, from_graph_id: r.from_graph_id,
      from_graph_name: r.from_graph_name, from_node_id: r.from_node_id, from_node_name: r.from_node_name, graph_id: r.graph_id,
      graph_name: r.graph_name, note: r.note, recorded_at: r.recorded_at, restarted_by: r.restarted_by,
      to_node_id: r.to_node_id, to_node_name: r.to_node_name })),
    route: detail.route && { environment_ids: [...detail.route.environment_ids], runner_id: detail.route.runner_id,
      runner_name: detail.route.runner_name, workspace_id: detail.route.workspace_id },
    status: laneStatus(detail.status),
    status_if_paused: detail.status_if_paused === null ? null : laneStatus(detail.status_if_paused),
    terminal: detail.terminal,
    urgency: detail.urgency && { slug: detail.urgency.slug, urgency_id: detail.urgency.urgency_id },
    usage: detail.usage.map((u) => ({ cache_create_tokens: u.cache_create_tokens, cache_read_tokens: u.cache_read_tokens,
      cost_partial: u.cost_partial, cost_usd: u.cost_usd, epoch: u.epoch, estimated_cost_usd: u.estimated_cost_usd,
      harness_id: u.harness_id, harness_version: u.harness_version, input_tokens: u.input_tokens, kind: fromUsageKind(u.kind), model: u.model,
      node_id: u.node_id, output_tokens: u.output_tokens, runner_id: u.runner_id })),
    work_refs: detail.work_refs.map((r) => ({ label: r.label, ref: r.ref, source: r.source, web_url: r.web_url })),
    awaitingHuman: toAwaitingHuman(detail),
    transcriptContext: {
      history: (detail.history ?? []).map((step) => ({
        from_node_id: step.from_node_id,
        from_node_name: step.from_node_name ?? null,
        epoch: step.epoch,
      })),
      current: {
        nodeId: detail.current_node_id,
        nodeName: detail.current_node_name ?? null,
        epoch: detail.latest_epoch,
        terminal: detail.current_node_terminal ?? false,
      },
    },
    neighborhood: detail.neighborhood && {
      prerequisites: detail.neighborhood.prerequisites.map(project),
      dependents: detail.neighborhood.dependents.map(project),
    },
  };
}

/** One chunk's aggregate through the target's own generated function — each plane calls
 * its own module (`bzh:generated-client`): the two specs declare the chunk-detail DTO
 * field for field, but calling through the wrong plane's function would make a future
 * divergence silent rather than a compile error. */
async function fetchChunkDetail(target: DaemonTarget, chunkId: string): Promise<WireChunkDetail> {
  const { data, error } =
    target.plane === 'hub'
      ? await hubApi.getChunkApiChunksChunkIdGet({ client: target.client, path: { chunk_id: chunkId }, throwOnError: false })
      : await runnerApi.getChunkRunnersRunnerIdApiChunksChunkIdGet({
          client: target.client,
          path: { runner_id: target.runnerId, chunk_id: chunkId },
          throwOnError: false,
        });
  if (error) throw error;
  return data!;
}

/** One chunk's work items through the target's own generated function — see {@link fetchChunkDetail}. */
async function fetchChunkWorkItems(target: DaemonTarget, chunkId: string): Promise<WorkItemsView> {
  const { data, error } =
    target.plane === 'hub'
      ? await hubApi.getWorkItemsApiChunksChunkIdWorkItemsGet({
          client: target.client,
          path: { chunk_id: chunkId },
          throwOnError: false,
        })
      : await runnerApi.getWorkItemsRunnersRunnerIdApiChunksChunkIdWorkItemsGet({
          client: target.client,
          path: { runner_id: target.runnerId, chunk_id: chunkId },
          throwOnError: false,
        });
  if (error) throw error;
  return data!;
}

/**
 * `GET /api/chunks/{chunk_id}` read — one chunk's full aggregate: its derived status,
 * current node, transition history, inline artifact store, and the `pause` fact.
 *
 * Target-generic: `target` is the daemon the read crosses to — it picks the generated
 * module, the cache key ({@link chunkDetailKey}, under the key root that daemon's
 * live-update registry invalidates), and the backstop interval. Reactive over the target
 * and the chunk id — disabled while either is `null`. Both are accessors for the same
 * reason `injectChunkTranscriptsQuery`'s are.
 */
export function injectChunkDetailQuery(target: () => DaemonTarget | null, chunkId: () => string | null) {
  return injectQuery(() => {
    const id = chunkId();
    const active = target();
    return {
      queryKey: chunkDetailKey(active === null ? '' : targetKeyRoot(active), id),
      enabled: id !== null && active !== null,
      queryFn: () => fetchChunkDetail(active!, id!),
      select: projectChunkDetail,
      // Live updates stale this key; see the hub's EVENT_INVALIDATION_REGISTRY (hub/core/live/hub-live-updates.ts) and polling.ts.
      refetchInterval: POLL_BACKSTOP_MS[active?.plane ?? 'hub'],
    };
  });
}

/**
 * `GET /api/chunks/{chunk_id}/work-items` read — the chunk's related work items: each
 * pointer's issue body and comment thread, fetched fresh from the forge and never
 * stored. A per-pointer forge failure carries an `error` rendered as a notice rather
 * than failing the whole read.
 *
 * Target-generic, the same seam as {@link injectChunkDetailQuery}. Unlike the aggregate
 * this does **not** poll: the read reaches an external forge (rate limits) and the issue
 * is stable for the life of an open view, so it fetches once and caches.
 */
export function injectChunkWorkItemsQuery(target: () => DaemonTarget | null, chunkId: () => string | null) {
  return injectQuery(() => {
    const id = chunkId();
    const active = target();
    return {
      queryKey: chunkWorkItemsKey(active === null ? '' : targetKeyRoot(active), id),
      enabled: id !== null && active !== null,
      queryFn: () => fetchChunkWorkItems(active!, id!),
      select: fromWorkItemsView,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    };
  });
}

/** The hub's own target — the hub client, under the `'hub'` key root. */
const HUB_TARGET: DaemonTarget = { plane: 'hub', client: hubClient };

/** The hub-plane aggregate read — a thin, permanently-hub-bound alias of
 * {@link injectChunkDetailQuery}. */
export function injectHubChunkDetailQuery(chunkId: () => string | null) {
  return injectChunkDetailQuery(() => HUB_TARGET, chunkId);
}

/** The hub-plane work-items read — see {@link injectHubChunkDetailQuery}. */
export function injectHubChunkWorkItemsQuery(chunkId: () => string | null) {
  return injectChunkWorkItemsQuery(() => HUB_TARGET, chunkId);
}
