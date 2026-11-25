import type { hubApi } from 'fleet';
import { unlisted } from 'fleet';
import type { EnvTimelineSegment, EnvTimelineStep, EnvTimelineView, InvocationKind, PlacedStep, StepExecutor } from './timeline.domain';

export function fromStepExecutor(value: hubApi.Executor | null): StepExecutor | null {
  switch (value) {
    case null: return null;
    case 'runner':
    case 'hub': return { kind: value };
    default: return { kind: 'unknown', raw: unlisted(value) };
  }
}

export function fromInvocationKind(value: hubApi.UsageKind): InvocationKind {
  switch (value) {
    case 'spawn':
    case 'resume':
    case 'judge': return { kind: value };
    default: return { kind: 'unknown', raw: unlisted(value) };
  }
}

function segment(value: hubApi.EnvTimelineSegmentView): EnvTimelineSegment {
  return { ...value, executor: fromStepExecutor(value.executor) };
}

function placed(value: hubApi.EnvTimelinePlacedStepView): PlacedStep {
  return { ...value, segment: segment(value.segment) };
}

/** Expand the compact timeline tables before caching the live catch-up view. */
export function expandTimeline(view: hubApi.EnvTimelineView): EnvTimelineView {
  return {
    since: view.since,
    until: view.until,
    generated_at: view.generated_at,
    hosts: view.hosts.map((host) => ({
      host_id: host.host_id,
      host_name: host.host_name,
      runner_names: [...host.runner_names],
      workspaces: host.workspaces.map((ws) => ({
        workspace_id: ws.workspace_id,
        environments: ws.environments.map((env) => ({
          environment_id: env.environment_id,
          segments: env.blocks.map((block): EnvTimelineSegment => {
            const chunk = view.chunks[block.chunk_id];
            const node = view.nodes[block.node_id];
            return {
              ...block,
              executor: fromStepExecutor(block.executor),
              title: chunk?.title ?? null,
              project_id: chunk?.project_id ?? null,
              runner_name: view.runners[block.runner_id] ?? block.runner_id,
              graph_id: null,
              graph_name: node?.graph_name ?? null,
              node_name: node?.node_name ?? block.node_id,
            };
          }),
        })),
      })),
    })),
  };
}

export function fromTimelineStep(view: hubApi.EnvTimelineStepView): EnvTimelineStep {
  return {
    artifact_keys: [...view.artifact_keys],
    escalations: view.escalations.map((entry) => ({ ...entry })),
    generated_at: view.generated_at,
    invocations: view.invocations.map((entry) => ({ ...entry, kind: fromInvocationKind(entry.kind) })),
    journey: view.journey.map(placed),
    spend: view.spend && { ...view.spend },
    step: placed(view.step),
  };
}
