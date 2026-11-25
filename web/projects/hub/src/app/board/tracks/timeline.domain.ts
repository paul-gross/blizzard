import type { ChunkDetailAggregate } from 'fleet';

export type StepExecutor =
  | { readonly kind: 'runner' | 'hub' }
  | { readonly kind: 'unknown'; readonly raw: string };

export interface EnvTimelineSegment {
  readonly chunk_id: string;
  readonly cost_estimated: boolean;
  readonly cost_usd: number;
  readonly ended_at: string | null;
  readonly epoch: number | null;
  readonly escalation: string | null;
  readonly escalation_open: boolean;
  readonly executor: StepExecutor | null;
  readonly exit_choice: string | null;
  readonly failed: boolean;
  readonly graph_id: string | null;
  readonly graph_name: string | null;
  readonly lease_id: string | null;
  readonly node_id: string;
  readonly node_name: string;
  readonly project_id: string | null;
  readonly runner_id: string;
  readonly runner_name: string;
  readonly started_at: string;
  readonly title: string | null;
}

export interface PlacedStep {
  readonly environment_id: string;
  readonly host_id: string | null;
  readonly host_name: string | null;
  readonly segment: EnvTimelineSegment;
  readonly workspace_id: string;
}

export interface EnvTimelineEnvironment {
  readonly environment_id: string;
  readonly segments: readonly EnvTimelineSegment[];
}

export interface EnvTimelineWorkspace {
  readonly workspace_id: string;
  readonly environments: readonly EnvTimelineEnvironment[];
}

export interface EnvTimelineHost {
  readonly host_id: string | null;
  readonly host_name: string | null;
  readonly runner_names: readonly string[];
  readonly workspaces: readonly EnvTimelineWorkspace[];
}

export interface EnvTimelineView {
  readonly since: string;
  readonly until: string;
  readonly generated_at: string;
  readonly hosts: readonly EnvTimelineHost[];
}

export type InvocationKind =
  | { readonly kind: 'spawn' | 'resume' | 'judge' }
  | { readonly kind: 'unknown'; readonly raw: string };

export interface TimelineInvocation {
  readonly cache_create_tokens: number;
  readonly cache_read_tokens: number;
  readonly cost_usd: number | null;
  readonly estimated_cost_usd: number | null;
  readonly harness_id: string | null;
  readonly input_tokens: number;
  readonly kind: InvocationKind;
  readonly model: string | null;
  readonly output_tokens: number;
  readonly recorded_at: string;
}

export interface EnvTimelineStep {
  readonly artifact_keys: readonly string[];
  readonly escalations: readonly { readonly cause: string | null; readonly detail: string | null; readonly recorded_at: string }[];
  readonly generated_at: string;
  readonly invocations: readonly TimelineInvocation[];
  readonly journey: readonly PlacedStep[];
  readonly spend: ChunkDetailAggregate['cost'] | null;
  readonly step: PlacedStep;
}
