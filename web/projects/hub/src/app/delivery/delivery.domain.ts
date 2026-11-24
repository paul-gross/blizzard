// bzh:frontend-domain-boundary — what the Delivery page reads of the delivery pipeline; no wire, no Angular.
import type { ChunkDetailAggregate, LaneStatus, WorkItem } from 'fleet';

/** One fact on a flow's record — the shared chunk-detail shape, so the list and the drawer read one fact. */
export type DeliveryFlowFact = ChunkDetailAggregate['flow_record'][number];

/** One of a chunk's places on a flow. */
export type DeliveryFlowPlace = ChunkDetailAggregate['flow_places'][number];

/** A recorded bounce. */
export type DeliveryBounceRecord = ChunkDetailAggregate['bounces'][number];

/** A graph transition of the chunk. */
export type DeliveryTransition = ChunkDetailAggregate['history'][number];

/** A pull request a chunk opened or closed. */
export type DeliveryPr = ChunkDetailAggregate['open_prs'][number];

/** A repository a chunk landed on. */
export type DeliveryLandedRepo = ChunkDetailAggregate['landed_repos'][number];

/** A pointer at a chunk's work item. */
export type DeliveryWorkRef = ChunkDetailAggregate['work_refs'][number];

/** The only fields of a flow the Delivery page reads. */
export interface DeliveryFlow {
  readonly flow_id: string;
  readonly repository_id: string;
}

/** The repository a flow lands on, as the page names it. */
export interface DeliveryRepository {
  readonly repository_id: string;
  readonly name: string;
  readonly owner: string;
  readonly repo: string;
}

/**
 * The board-summary projection the Delivery list reads. `terminal`, `landed` and the places' `in_line` are carried
 * from the hub's own answer and never re-derived.
 */
export interface DeliveryChunk {
  readonly chunk_id: string;
  readonly laneStatus: LaneStatus;
  readonly terminal: boolean;
  readonly landed: boolean;
  readonly landed_repos: readonly DeliveryLandedRepo[];
  readonly open_prs: readonly DeliveryPr[];
  readonly closed_prs: readonly DeliveryPr[];
  readonly flow_places: readonly DeliveryFlowPlace[];
  readonly awaiting_external_merge: boolean;
  readonly latest_bounce: DeliveryBounceRecord | null;
  readonly bounce_count: number;
  readonly work_refs: readonly DeliveryWorkRef[];
  readonly project_id: string | null;
  readonly project_name: string | null;
  readonly completed_at: string | null;
  readonly current_node_id: string | null;
  readonly current_node_name: string | null;
  readonly graph_name: string | null;
  readonly runner_id: string | null;
  readonly runner_name: string | null;
}

/** A chunk's work items, as the list's titles read them. */
export interface DeliveryWorkItems {
  readonly items: readonly WorkItem[];
}
