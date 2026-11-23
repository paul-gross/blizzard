import { graphEdgeView, graphNodeView, graphSummaryView, graphView } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { choiceTargetKind, executor, fromGraphSummaryView, fromGraphView, graphLifecycle, judgedBy } from './graph.translate';

const SUMMARY = graphSummaryView({
  graph_id: 'gr_build',
  name: 'build',
  created_at: '2026-07-18T00:00:00Z',
  effective: false,
  retired: true,
  lifecycle: 'retired',
});

describe('graph vocabularies', () => {
  it('reads each known choice target kind as itself', () => {
    expect(['node', 'done', 'graph'].map(choiceTargetKind)).toEqual(['node', 'done', 'graph']);
  });

  it('keeps the reserved escalate target distinct from done', () => {
    expect(choiceTargetKind('escalate')).toBe('escalate');
  });

  it('keeps an unforeseen value of every vocabulary raw', () => {
    expect(choiceTargetKind('portal')).toEqual({ kind: 'unknown', raw: 'portal' });
    expect(executor('cloud')).toEqual({ kind: 'unknown', raw: 'cloud' });
    expect(judgedBy('oracle')).toEqual({ kind: 'unknown', raw: 'oracle' });
    expect(graphLifecycle('archived')).toEqual({ kind: 'unknown', raw: 'archived' });
  });

  it('reads each known executor, judge, and lifecycle as itself', () => {
    expect([executor('runner'), executor('hub')]).toEqual(['runner', 'hub']);
    expect([judgedBy('worker'), judgedBy('human')]).toEqual(['worker', 'human']);
    expect(['effective', 'superseded', 'retired'].map(graphLifecycle)).toEqual(['effective', 'superseded', 'retired']);
  });
});

describe('fromGraphSummaryView', () => {
  it("carries the hub's effective, retired, and lifecycle judgments as served", () => {
    expect(fromGraphSummaryView(SUMMARY)).toEqual({
      graphId: 'gr_build',
      name: 'build',
      createdAt: '2026-07-18T00:00:00Z',
      effective: false,
      retired: true,
      lifecycle: 'retired',
    });
  });
});

describe('fromGraphView', () => {
  it('translates nodes, edges, and sessions, keeping produces as names', () => {
    const graph = fromGraphView(
      graphView({
        retired: true,
        nodes: [
          graphNodeView({
            executor: 'hub',
            judged_by: 'human',
            session: 'resume',
            session_source: 'code',
            action_args: { repo: ['a', 'b'] },
            produces: [{ name: 'plan', kind: 'asset' }],
            retries_max: 2,
            retries_exhausted: 'escalate',
            choices: [{ choice_id: 'ch_pass', name: 'pass', description: 'Done.', requires_checks: false }],
          }),
        ],
        edges: [graphEdgeView({ target_kind: 'graph', target_graph: 'deliver', prompt_addendum: 'Go.' })],
        sessions: [
          {
            name: 'code',
            model: ['blizzard:basic'],
            harnesses: ['claude_code'],
            effort: 'high',
            compaction_window: null,
            rotate: { max_context_tokens: 1000, max_invocations: null, max_transcript_bytes: null },
          },
        ],
      }),
    );

    expect(graph.retired).toBe(true);
    expect(graph.nodes[0]).toEqual({
      nodeId: 'nd_build',
      name: 'build',
      executor: 'hub',
      session: 'resume',
      sessionSource: 'code',
      judgedBy: 'human',
      judgementPrompt: null,
      action: null,
      actionArgs: { repo: ['a', 'b'] },
      prompt: null,
      checks: [],
      produces: ['plan'],
      retriesMax: 2,
      retriesExhausted: 'escalate',
      choices: [{ choiceId: 'ch_pass', name: 'pass', description: 'Done.' }],
    });
    expect(graph.edges[0]).toEqual({
      choiceId: 'ch_pass',
      fromNodeId: 'nd_build',
      toNodeName: 'review',
      targetKind: 'graph',
      targetGraph: 'deliver',
      promptAddendum: 'Go.',
    });
    expect(graph.sessions[0].rotate).toEqual({ maxContextTokens: 1000, maxInvocations: null, maxTranscriptBytes: null });
  });

  it('reads an unforeseen executor, judge, and target kind as unknown', () => {
    const graph = fromGraphView(
      graphView({
        nodes: [graphNodeView({ executor: 'cloud' as never, judged_by: 'oracle' as never })],
        edges: [graphEdgeView({ target_kind: 'portal' as never })],
      }),
    );

    expect(graph.nodes[0].executor).toEqual({ kind: 'unknown', raw: 'cloud' });
    expect(graph.nodes[0].judgedBy).toEqual({ kind: 'unknown', raw: 'oracle' });
    expect(graph.edges[0].targetKind).toEqual({ kind: 'unknown', raw: 'portal' });
  });

  it('reads an escalate edge as escalate, not done', () => {
    const graph = fromGraphView(graphView({ edges: [graphEdgeView({ target_kind: 'escalate' })] }));

    expect(graph.edges[0].targetKind).toBe('escalate');
  });

  it('reads a graph with no nodes, edges, or sessions as empty lists', () => {
    const graph = fromGraphView(graphView({ nodes: [], edges: [], sessions: [] }));

    expect(graph.nodes).toEqual([]);
    expect(graph.edges).toEqual([]);
    expect(graph.sessions).toEqual([]);
  });
});
