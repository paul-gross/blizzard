import { fromFlowFactView } from 'fleet/chunk-page';
import { type ChunkFlowPlaceView } from 'fleet/hub-api';
import { bounceView, chunkSummary, flowFactView, flowView, landedRepoView, prView, repositorySummary, workRefView } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { fromChunkSummary, fromFlowView, fromRepositorySummary } from './delivery.translate';

const placeView = (fields: Partial<ChunkFlowPlaceView> = {}): ChunkFlowPlaceView => ({
  flow_id: 'fl_a', position: 1, state: 'waiting', in_line: true, branch: 'master', next_poll_at: null,
  repository_name: null, repository_slug: null, resource: 'rp_a:master', ...fields,
});

describe('fromFlowView', () => {
  it('carries the flow and repository ids', () => {
    expect(fromFlowView(flowView({ flow_id: 'fl_x', repository_id: 'rp_x' }))).toEqual({ flow_id: 'fl_x', repository_id: 'rp_x' });
  });
});

describe('fromRepositorySummary', () => {
  it('carries the id, name, owner and repo', () => {
    expect(fromRepositorySummary(repositorySummary({ repository_id: 'rp_x', name: 'core', owner: 'acme', repo: 'core-repo' }))).toEqual({
      repository_id: 'rp_x', name: 'core', owner: 'acme', repo: 'core-repo',
    });
  });
});

describe('fromChunkSummary', () => {
  const wire = chunkSummary({
    chunk_id: 'ch_1',
    graph_id: 'gr_1',
    status: 'needs_human',
    terminal: true,
    landed: true,
    landed_repos: [landedRepoView()],
    open_prs: [prView({ number: 1 })],
    closed_prs: [prView({ number: 2 })],
    flow_places: [placeView({ flow_id: 'fl_a', state: 'landing', in_line: false })],
    awaiting_external_merge: true,
    latest_bounce: bounceView({ cause: 'conflict', rework: true, withdrawn: true }),
    bounce_count: 3,
    work_refs: [workRefView({ ref: '9' })],
    project_id: 'pj_1',
    project_name: 'Fleet',
    completed_at: '2026-07-15T00:00:00Z',
    current_node_id: 'nd_1',
    current_node_name: 'build',
    graph_name: 'adv',
    runner_id: 'rn_1',
    runner_name: 'r-claude',
  });

  it('carries every field the list reads, and the hub\'s own terminal and landed', () => {
    const chunk = fromChunkSummary(wire);
    expect(chunk).toMatchObject({
      chunk_id: 'ch_1', laneStatus: { kind: 'needs_human' }, terminal: true, landed: true, awaiting_external_merge: true,
      bounce_count: 3, project_id: 'pj_1', project_name: 'Fleet', completed_at: '2026-07-15T00:00:00Z',
      current_node_id: 'nd_1', current_node_name: 'build', graph_name: 'adv', runner_id: 'rn_1', runner_name: 'r-claude',
    });
    expect(chunk.landed_repos).toEqual([{ commit_hash: landedRepoView().commit_hash, repo: landedRepoView().repo, url: landedRepoView().url }]);
    expect(chunk.open_prs.map((pr) => pr.number)).toEqual([1]);
    expect(chunk.closed_prs.map((pr) => pr.number)).toEqual([2]);
    expect(chunk.flow_places[0]).toMatchObject({ flow_id: 'fl_a', state: { kind: 'landing' }, in_line: false });
    expect(chunk.latest_bounce).toMatchObject({ cause: 'conflict', rework: true, withdrawn: true });
    expect(chunk.work_refs).toEqual([{ label: workRefView({ ref: '9' }).label, ref: '9', source: workRefView().source, web_url: workRefView().web_url }]);
  });

  it('keeps nullable fields null and does not re-derive terminal or landed', () => {
    const chunk = fromChunkSummary(chunkSummary({ chunk_id: 'ch_2', graph_id: 'gr_1', status: 'done', terminal: false, landed: false }));
    expect(chunk).toMatchObject({ latest_bounce: null, project_id: null, completed_at: null, runner_id: null, terminal: false, landed: false });
  });

  it('reads an unknown chunk status, place state and fact kind or verdict as the unknown member', () => {
    const chunk = fromChunkSummary(chunkSummary({
      chunk_id: 'ch_3', graph_id: 'gr_1', status: 'future' as never,
      flow_places: [placeView({ state: 'mystery' as never })],
    }));
    expect(chunk.laneStatus).toEqual({ kind: 'unknown', raw: 'future' });
    expect(chunk.flow_places[0].state).toEqual({ kind: 'unknown', raw: 'mystery' });
  });
});

describe('flow facts the delivery query reads', () => {
  it('reads an unknown fact kind and verdict as the unknown member', () => {
    const fact = fromFlowFactView(flowFactView({ kind: 'rewound' as never, verdict: 'maybe' as never }));
    expect(fact.kind).toEqual({ kind: 'unknown', raw: 'rewound' });
    expect(fact.verdict).toEqual({ kind: 'unknown', raw: 'maybe' });
  });
});
