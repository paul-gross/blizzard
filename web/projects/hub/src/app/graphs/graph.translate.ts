// bzh:frontend-domain-boundary — the one place the graph wire is read.
import { openLookup } from 'fleet/core';
import {
  hubApi,
  type GraphChoiceView,
  type GraphLifecycle as WireGraphLifecycle,
  type GraphNodeView,
  type GraphSessionView,
  type GraphSummaryView,
  type GraphView,
} from 'fleet/hub-api';

import type {
  ChoiceTargetKind,
  Executor,
  Graph,
  GraphChoice,
  GraphEdge,
  GraphLifecycle,
  GraphNode,
  GraphSession,
  GraphSummary,
  JudgedBy,
  UnknownGraphValue,
} from './graph.domain';

const unknown = (raw: string): UnknownGraphValue => ({ kind: 'unknown', raw });

/** An edge's target kind, `escalate` kept distinct: it parks the chunk at its node rather than finishing it. */
export const choiceTargetKind = openLookup<hubApi.ChoiceTargetKind, ChoiceTargetKind>(
  { node: 'node', done: 'done', graph: 'graph', escalate: 'escalate' },
  unknown,
);

export const executor = openLookup<hubApi.Executor, Executor>({ runner: 'runner', hub: 'hub' }, unknown);

export const judgedBy = openLookup<hubApi.JudgedBy, JudgedBy>({ worker: 'worker', human: 'human' }, unknown);

export const graphLifecycle = openLookup<WireGraphLifecycle, GraphLifecycle>(
  { effective: 'effective', superseded: 'superseded', retired: 'retired' },
  unknown,
);

/** A graph-list row as the domain holds it; the hub's `effective`, `retired`, and `lifecycle` judgments carried as served. */
export function fromGraphSummaryView(wire: GraphSummaryView): GraphSummary {
  return {
    graphId: wire.graph_id,
    name: wire.name,
    createdAt: wire.created_at,
    effective: wire.effective,
    retired: wire.retired,
    lifecycle: graphLifecycle(wire.lifecycle),
  };
}

function fromChoiceView(wire: GraphChoiceView): GraphChoice {
  return { choiceId: wire.choice_id, name: wire.name, description: wire.description };
}

function fromNodeView(wire: GraphNodeView): GraphNode {
  return {
    nodeId: wire.node_id,
    name: wire.name,
    executor: executor(wire.executor),
    session: wire.session,
    sessionSource: wire.session_source ?? null,
    judgedBy: judgedBy(wire.judged_by),
    judgementPrompt: wire.judgement_prompt ?? null,
    action: wire.action ?? null,
    actionArgs: wire.action_args ?? {},
    prompt: wire.prompt ?? null,
    checks: wire.checks ?? [],
    produces: (wire.produces ?? []).map((p) => p.name),
    retriesMax: wire.retries_max ?? null,
    retriesExhausted: wire.retries_exhausted ?? null,
    choices: (wire.choices ?? []).map(fromChoiceView),
  };
}

function fromEdgeView(wire: hubApi.GraphEdgeView): GraphEdge {
  return {
    choiceId: wire.choice_id,
    fromNodeId: wire.from_node_id,
    toNodeName: wire.to_node_name,
    targetKind: choiceTargetKind(wire.target_kind ?? 'node'),
    targetGraph: wire.target_graph ?? null,
    promptAddendum: wire.prompt_addendum ?? null,
  };
}

function fromSessionView(wire: GraphSessionView): GraphSession {
  return {
    name: wire.name,
    model: wire.model ?? [],
    harnesses: wire.harnesses ?? [],
    effort: wire.effort ?? null,
    compactionWindow: wire.compaction_window ?? null,
    rotate: wire.rotate
      ? {
          maxContextTokens: wire.rotate.max_context_tokens ?? null,
          maxInvocations: wire.rotate.max_invocations ?? null,
          maxTranscriptBytes: wire.rotate.max_transcript_bytes ?? null,
        }
      : null,
  };
}

/** A minted graph as the domain holds it; its `retired` flag carried as served. Lists an older hub omits read empty. */
export function fromGraphView(wire: GraphView): Graph {
  return {
    graphId: wire.graph_id,
    name: wire.name,
    entryNodeId: wire.entry_node_id,
    retired: wire.retired ?? false,
    nodes: (wire.nodes ?? []).map(fromNodeView),
    edges: (wire.edges ?? []).map(fromEdgeView),
    sessions: (wire.sessions ?? []).map(fromSessionView),
  };
}
