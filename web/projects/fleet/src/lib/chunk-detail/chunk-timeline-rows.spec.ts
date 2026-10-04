import type { ChunkDetail } from '../api/hub';
import { deriveHistoryRows, deriveMultiGraph, rowChoice, rowMark, usageForStep } from './chunk-timeline-rows';

const BASE: ChunkDetail = {
  chunk_id: 'ch_01rows00000000000000000000000',
  graph_id: 'gr_2',
  graph_name: 'second',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 3,
  work_refs: [],
  artifacts: [],
  history: [
    { from_node_id: 'nd_build', to_node_id: 'nd_review', choice_name: 'pass', epoch: 1, recorded_at: '2026-07-13T00:00:01Z' },
    { from_node_id: 'nd_review', to_node_id: 'nd_build', choice_name: 'fail', epoch: 2, recorded_at: '2026-07-13T00:00:05Z' },
  ],
  migrations: [
    {
      epoch: 2,
      from_graph_id: 'gr_1',
      from_node_id: 'nd_review',
      to_graph_id: 'gr_2',
      landed_node_id: 'nd_build',
      source: 'authored-edge',
      choice_name: 'escalate',
      recorded_at: '2026-07-13T00:00:03Z',
    },
  ],
  bounces: [{ cause: 'schema-mismatch', envelope: '{"verdict":"???"}', recorded_at: '2026-07-13T00:00:02Z' }],
  restarts: [
    {
      epoch: 3,
      graph_id: 'gr_2',
      graph_name: 'second',
      from_node_id: 'nd_build',
      from_node_name: 'build',
      to_node_id: 'nd_plan',
      to_node_name: 'plan',
      restarted_by: 'operator@example.test',
      recorded_at: '2026-07-13T00:00:04Z',
    },
  ],
} as ChunkDetail;

describe('deriveHistoryRows', () => {
  it('interleaves transitions, migrations, bounces and restarts by recorded_at', () => {
    expect(deriveHistoryRows(BASE).map((r) => r.kind)).toEqual(['transition', 'bounce', 'migration', 'restart', 'transition']);
  });

  it('keys neither a bounce nor a restart', () => {
    const rows = deriveHistoryRows(BASE).filter((r) => r.kind === 'bounce' || r.kind === 'restart');
    expect(rows.map((r) => r.key)).toEqual([null, null]);
  });

  it('reads a bounce as its cause with the raw envelope as its title, routing nowhere', () => {
    const bounce = deriveHistoryRows(BASE).find((r) => r.kind === 'bounce')!;
    expect(bounce).toMatchObject({ verdict: 'schema-mismatch', title: '{"verdict":"???"}', toName: null, epoch: null });
    expect(rowMark(bounce)).toBe('\u21A9\uFE0E');
    expect(rowChoice(bounce)).toBe('bounced');
  });

  it('reads a same-graph restart as from → to, by its operator, with no usage of its own', () => {
    const detail = {
      ...BASE,
      usage: [{ node_id: 'nd_build', epoch: 3, kind: 'spawn', model: 'm', input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_create_tokens: 0, cost_usd: 0.01 }],
    } as ChunkDetail;
    const restart = deriveHistoryRows(detail).find((r) => r.kind === 'restart')!;
    expect(restart).toMatchObject({ nodeName: 'build', toName: 'plan', actor: 'operator@example.test', crossesGraph: false });
    expect(rowChoice(restart)).toBe('restarted');
    expect(usageForStep(detail, restart)).toBeNull();
  });

  it('folds a cross-graph restart’s own migration into the one restart row', () => {
    const detail = {
      ...BASE,
      migrations: [
        ...BASE.migrations!,
        {
          epoch: 3,
          from_graph_id: 'gr_1',
          from_node_id: 'nd_build',
          to_graph_id: 'gr_2',
          landed_node_id: 'nd_plan',
          source: 'restart',
          recorded_at: '2026-07-13T00:00:04Z',
        },
      ],
      restarts: [{ ...BASE.restarts![0], from_graph_id: 'gr_1', from_graph_name: 'first' }],
    } as ChunkDetail;
    const rows = deriveHistoryRows(detail);
    expect(rows.filter((r) => r.kind === 'migration')).toHaveLength(1);
    const restart = rows.find((r) => r.kind === 'restart')!;
    expect(restart).toMatchObject({ graphName: 'first', graphId: 'gr_1', toName: 'second/plan', crossesGraph: true });
  });

  it('counts a graph-crossing restart as multi-graph on its own', () => {
    const detail = {
      ...BASE,
      history: [],
      migrations: [],
      bounces: [],
      restarts: [{ ...BASE.restarts![0], from_graph_id: 'gr_1' }],
    } as ChunkDetail;
    expect(deriveMultiGraph(deriveHistoryRows(detail))).toBe(true);
  });

  it('renders unchanged when bounces and restarts are absent', () => {
    const rest = { ...BASE, bounces: undefined, restarts: undefined };
    expect(deriveHistoryRows(rest).map((r) => r.kind)).toEqual(['transition', 'migration', 'transition']);
  });
});
