// The delivery domain builders specs build their inputs from — domain literals only, no wire, no Angular.
// The wire builders (`chunkSummary`, `flowView`, `flowFactView`, `repositorySummary`, `bounceView` in `fleet/testing`)
// stay for the translator spec and stubbed answers.
import { fixtureInstant, workItem } from 'fleet/testing';

import type { DeliveryChunk, DeliveryFlow, DeliveryFlowFact, DeliveryRepository, DeliveryWorkItems } from './delivery.domain';


/** A running chunk with no delivery footprint. */
export function deliveryChunk(overrides: Partial<DeliveryChunk> = {}): DeliveryChunk {
  return {
    chunk_id: 'ch_01',
    laneStatus: { kind: 'running' },
    terminal: false,
    landed: false,
    landed_repos: [],
    open_prs: [],
    closed_prs: [],
    flow_places: [],
    awaiting_external_merge: false,
    latest_bounce: null,
    bounce_count: 0,
    work_refs: [],
    project_id: null,
    project_name: null,
    completed_at: null,
    current_node_id: null,
    current_node_name: null,
    graph_name: null,
    runner_id: null,
    runner_name: null,
    ...overrides,
  };
}

/** A flow landing on the fixture repository. */
export function deliveryFlow(overrides: Partial<DeliveryFlow> = {}): DeliveryFlow {
  return { flow_id: 'fl_a', repository_id: 'rp_a', ...overrides };
}

export function deliveryRepository(overrides: Partial<DeliveryRepository> = {}): DeliveryRepository {
  return { repository_id: 'rp_a', name: 'blizzard', owner: 'paul-gross', repo: 'blizzard', ...overrides };
}

/** A submitted fact a minute ago on the fixture flow. */
export function deliveryFlowFact(overrides: Partial<DeliveryFlowFact> = {}): DeliveryFlowFact {
  return {
    at: fixtureInstant(-60),
    chunk_id: 'ch_01',
    flow_id: 'fl_a',
    id: 1,
    kind: { kind: 'submitted' },
    outcome: null,
    revision: null,
    rework: false,
    submission_id: 'sub-ch_01',
    verdict: null,
    wait: false,
    ...overrides,
  };
}

export function deliveryWorkItems(overrides: Partial<DeliveryWorkItems> = {}): DeliveryWorkItems {
  return { items: [workItem()], ...overrides };
}

