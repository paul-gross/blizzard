// bzh:frontend-domain-boundary — the one place the delivery wire is read.
import { fromBounceView, fromChunkFlowPlaceView } from 'fleet/chunk-page';
import { type ChunkSummary, type FlowView, type RepositorySummary } from 'fleet/hub-api';
import { laneStatus } from 'fleet/shell';

import type { DeliveryChunk, DeliveryFlow, DeliveryRepository } from './delivery.domain';

export function fromFlowView(wire: FlowView): DeliveryFlow {
  return { flow_id: wire.flow_id, repository_id: wire.repository_id };
}

export function fromRepositorySummary(wire: RepositorySummary): DeliveryRepository {
  return { repository_id: wire.repository_id, name: wire.name, owner: wire.owner, repo: wire.repo };
}

export function fromChunkSummary(wire: ChunkSummary): DeliveryChunk {
  return {
    chunk_id: wire.chunk_id,
    laneStatus: laneStatus(wire.status),
    terminal: wire.terminal,
    landed: wire.landed,
    landed_repos: wire.landed_repos.map((r) => ({ commit_hash: r.commit_hash, repo: r.repo, url: r.url })),
    open_prs: wire.open_prs.map((p) => ({ number: p.number, repo: p.repo, url: p.url })),
    closed_prs: wire.closed_prs.map((p) => ({ number: p.number, repo: p.repo, url: p.url })),
    flow_places: wire.flow_places.map(fromChunkFlowPlaceView),
    awaiting_external_merge: wire.awaiting_external_merge,
    latest_bounce: wire.latest_bounce === null ? null : fromBounceView(wire.latest_bounce),
    bounce_count: wire.bounce_count,
    work_refs: wire.work_refs.map((r) => ({ label: r.label, ref: r.ref, source: r.source, web_url: r.web_url })),
    project_id: wire.project_id,
    project_name: wire.project_name,
    completed_at: wire.completed_at,
    current_node_id: wire.current_node_id,
    current_node_name: wire.current_node_name,
    graph_name: wire.graph_name,
    runner_id: wire.runner_id,
    runner_name: wire.runner_name,
  };
}
