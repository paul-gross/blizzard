import type { LaneStatus } from '../../core/chunk-lanes';
import type { TranscriptContext } from '../../transcripts/transcript-turn';
import type { AwaitingHuman } from '../awaiting-human/awaiting-human.domain';
import type { ChunkArtifact } from '../chunk-detail/artifact.model';
import type { ChunkEscalationState } from '../chunk-detail/escalation.model';

export type ChunkStatus = LaneStatus;
export type MigrationSource = { kind: 'authored-edge' | 'intent' | 'follow-latest' | 'restart' } | { kind: 'unknown'; raw: string };
export type MigrationMode = { kind: 'auto' | 'forced' } | { kind: 'unknown'; raw: string };
export type UsageKind = { kind: 'spawn' | 'resume' | 'judge' } | { kind: 'unknown'; raw: string };
export type FlowState = { kind: 'landing' | 'next' | 'waiting' | 'polling' | 'awaiting-merge' | 'passed-over' } | { kind: 'unknown'; raw: string };
export type FlowFactKind = { kind: 'submitted' | 'turn-started' | 'turn-ended' | 'landed' | 'withdrawn' } | { kind: 'unknown'; raw: string };
export type FlowVerdict = { kind: 'landed' | 'bounced' } | { kind: 'unknown'; raw: string };

export interface ChunkTransition {
  choice_name: string | null; epoch: number; from_node_id: string | null; from_node_name: string | null;
  graph_id: string | null; graph_name: string | null; recorded_at: string; to_node_id: string; to_node_name: string | null;
}
export interface ChunkUsageTotal {
  billed_partial: boolean; cache_create_tokens: number; cache_read_tokens: number; cost_partial: boolean;
  cost_usd: number; estimated_cost_usd: number | null; input_tokens: number; output_tokens: number;
}
export interface ChunkUsage {
  cache_create_tokens: number; cache_read_tokens: number; cost_partial: boolean; cost_usd: number | null; epoch: number;
  estimated_cost_usd: number | null; harness_id: string | null; harness_version: string | null; input_tokens: number;
  kind: UsageKind; model: string; node_id: string; output_tokens: number; runner_id: string | null;
}
export interface ChunkDecision {
  check_results: { command: string; passed: boolean }[];
  choices: { description: string; name: string; requires_checks: boolean; would_override_red_checks: boolean }[];
  chunk_id: string; decision_id: string; epoch: number; imposed_by_runner_id: string | null; imposed_by_runner_name: string | null;
  node_id: string; node_name: string; note: string | null; overrode_red_checks: boolean; project_id: string | null;
  project_name: string | null; project_slug: string | null; resolved_at: string | null; resolved_by: string | null;
  resolved_choice: string | null; submitted_at: string; transitioned: boolean;
}
export interface ChunkQuestion {
  answer: string | null; answered: boolean; answered_at: string | null; answered_by: string | null; asked_at: string;
  chunk_id: string; delivered: boolean; delivered_at: string | null; epoch: number; harness_id: string | null;
  node_id: string | null; options: string[]; project_id: string | null; project_name: string | null;
  project_slug: string | null; question: string; question_id: string; runner_id: string; runner_name: string | null;
  session_id: string | null; superseded: boolean;
}
export interface ChunkPr { number: number; repo: string; url: string; }
export interface ChunkLandedRepo { commit_hash: string; repo: string; url: string | null; }
export interface ChunkRoute { environment_ids: string[]; runner_id: string; runner_name: string | null; workspace_id: string; }
export interface ChunkWorkRef { label: string | null; ref: string; source: string; web_url: string | null; }
export interface ChunkPause { by: string; set_at: string; }
export type ChunkWaitKind = { kind: 'pending' | 'awaiting-merge' } | { kind: 'unknown'; raw: string };
export type ChunkDeleteRefusal = { kind: 'status' | 'dependents' } | { kind: 'unknown'; raw: string };
export interface ChunkPending { kind: ChunkWaitKind; next_poll_at: string; node_name: string; since: string | null; }
export interface ChunkNeighbor { chunk_id: string; satisfied: boolean; laneStatus: LaneStatus | null; }
export interface ChunkMigration {
  choice_name: string | null; epoch: number; from_graph_id: string; from_graph_name: string | null; from_node_id: string | null;
  from_node_name: string | null; landed_node_id: string | null; landed_node_name: string | null; model: string | null;
  recorded_at: string; source: MigrationSource | null; to_graph_id: string; to_graph_name: string | null;
}
export interface ChunkDetail {
  artifacts: ChunkArtifact[]; awaiting_external_merge: boolean; blocked: { prerequisite_chunk_id: string; unmet_count: number } | null;
  bounces: { cause: string; envelope: string; recorded_at: string; rework: boolean; withdrawn: boolean }[];
  chunk_id: string; closed_prs: ChunkPr[]; completable: boolean; cost: ChunkUsageTotal; current_node_id: string | null;
  current_node_name: string | null; current_node_terminal: boolean; default_effort: string | null;
  default_harnesses: string[]; default_model: string[]; deletable: boolean; delete_refusal: ChunkDeleteRefusal | null;
  escalation: ChunkEscalationState | null;
  flow_places: { branch: string; flow_id: string; in_line: boolean; next_poll_at: string | null; position: number;
    repository_name: string | null; repository_slug: string | null; resource: string;
    state: FlowState }[];
  flow_record: { at: string; chunk_id: string; flow_id: string; id: number;
    kind: FlowFactKind; outcome: string | null;
    revision: string | null; rework: boolean; submission_id: string; verdict: FlowVerdict | null; wait: boolean }[];
  graph_created_at: string | null; graph_editable: boolean; graph_id: string; graph_name: string | null;
  history: ChunkTransition[]; intended_migration: { graph_id: string; graph_name: string | null; mode: MigrationMode; node_name: string | null } | null;
  landed: boolean; landed_repos: ChunkLandedRepo[]; latest_epoch: number | null; migrations: ChunkMigration[];
  neighborhood: { dependents: ChunkNeighbor[]; prerequisites: ChunkNeighbor[] };
  open_prs: ChunkPr[]; pausable: boolean; pause: ChunkPause | null; pending: ChunkPending | null;
  project_id: string | null; project_name: string | null; project_slug: string | null; promotable: boolean;
  repository_targets: { base_branch: string; captured_at: string | null; inherited: boolean; repository_id: string; repository_slug: string }[];
  requeues: { note: string | null; requeued_at: string; requeued_by: string | null }[];
  resolutions: { choice: string; decision_id: string; epoch: number; node_id: string; node_name: string; note: string | null;
    overrode_red_checks: boolean; resolved_at: string; resolved_by: string }[];
  restarts: { decision_id: string | null; epoch: number; from_graph_id: string | null; from_graph_name: string | null;
    from_node_id: string | null; from_node_name: string | null; graph_id: string; graph_name: string | null;
    note: string | null; recorded_at: string; restarted_by: string; to_node_id: string; to_node_name: string | null }[];
  route: ChunkRoute | null; status: ChunkStatus; status_if_paused: ChunkStatus | null; terminal: boolean;
  urgency: { slug: string; urgency_id: string } | null; usage: ChunkUsage[]; work_refs: ChunkWorkRef[];
  awaitingHuman: AwaitingHuman; transcriptContext: TranscriptContext;
}
