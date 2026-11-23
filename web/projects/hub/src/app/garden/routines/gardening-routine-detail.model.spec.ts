import type { AsyncStateQuery } from 'fleet';
import { describe, expect, it } from 'vitest';
import type { Graph, GraphNode, GraphSummary } from '../../graphs/graph.domain';
import { fixtureInstant } from 'fleet/testing';

import { gardenRoutine, gardenRoutineKeeper } from '../core/routine.builders';
import {
  gardenMeasurementReading,
  gardenProposalCountsRow,
  gardenScopeLastSwept,
  gardenTrendPeriod,
} from '../core/routine-read.builders';
import type { RoutineProposalCounts, RoutineSweeps, RoutineTrend } from '../core/routine-read.domain';
import { gardenDeliveredSet, gardenRunRow } from '../core/run.builders';
import type { RunRow } from '../core/run.domain';
import type { Routine } from '../core/routine.domain';
import {
  keptByVms,
  modelEffortLabel,
  routineRecentRuns,
  routineRunsLabel,
  lastSweptRows,
  measurementReadings,
  proposalCountsRows,
  relatedScopeRows,
  routineBySlug,
  routineEffectiveGraph,
  routineOverrideRetired,
  whileEditing,
  routinePanelState,
  routinePanelVm,
  strategySteps,
  trendTotals,
  type RoutinePanelParts,
} from './gardening-routine-detail.model';

const ROUTINE: Routine = gardenRoutine({
  id: 'rtn_1',
  slug: 'nightly',
  name: 'Nightly sweep',
  graph: 'garden-routine',
  defaultModel: ['claude-sonnet-5'],
  defaultEffort: 'medium',
  revision: null,
});

const OTHER_ROUTINE: Routine = { ...ROUTINE, id: 'rtn_2', slug: 'weekly', name: 'Weekly sweep' };

const EFFECTIVE_GRAPH: GraphSummary = {
  graphId: 'gr_1',
  name: 'garden-routine',
  createdAt: '2026-01-01T00:00:00Z',
  effective: true,
  lifecycle: 'effective',
  retired: false,
};

const SUPERSEDED_GRAPH: GraphSummary = { ...EFFECTIVE_GRAPH, graphId: 'gr_0', effective: false, lifecycle: 'superseded' };

function node(fields: Pick<GraphNode, 'nodeId' | 'name'> & Partial<GraphNode>): GraphNode {
  return {
    executor: 'runner',
    session: 'fresh',
    judgedBy: 'worker',
    prompt: null,
    action: null,
    actionArgs: {},
    checks: [],
    choices: [],
    judgementPrompt: null,
    produces: [],
    retriesExhausted: null,
    retriesMax: null,
    sessionSource: null,
    ...fields,
  };
}

const GRAPH: Graph = {
  graphId: 'gr_1',
  name: 'garden-routine',
  entryNodeId: 'nd_1',
  nodes: [
    node({ nodeId: 'nd_1', name: 'survey', prompt: 'Survey the repo.' }),
    node({ nodeId: 'nd_2', name: 'report' }),
  ],
  edges: [],
  retired: false,
  sessions: [],
};

const SWEEPS: RoutineSweeps = {
  lastSwept: [
    gardenScopeLastSwept({
      scopeSlug: 'blizzard',
      findingSetId: 'fins_1',
      producedAt: '2026-01-10T00:00:00Z',
      revisions: { hub: 'def456', blizzard: 'abc123' },
    }),
    gardenScopeLastSwept({ scopeSlug: 'never-swept' }),
  ],
  measurements: [gardenMeasurementReading({ scopeSlug: 'blizzard', producedAt: '2026-01-10T00:00:00Z', measurement: '3 findings' })],
};

const TREND: RoutineTrend = {
  periods: [
    gardenTrendPeriod({ created: 2, outflow: 1, withdrawn: 0, reopened: 3 }),
    gardenTrendPeriod({ created: 4, outflow: 2, withdrawn: 1, reopened: 0 }),
  ],
};

const PROPOSAL_COUNTS: RoutineProposalCounts = {
  rows: [gardenProposalCountsRow({ open: 2, passed: 1, acceptedWithItem: 3, acceptedWithoutItem: 0, created: 6 })],
};

function query(state: { pending?: boolean; error?: boolean }): AsyncStateQuery {
  return { isPending: () => state.pending ?? false, isError: () => state.error ?? false };
}

const PARTS: RoutinePanelParts = {
  blocked: false,
  strategy: [{ name: 'survey', prompt: 'Survey the repo.' }],
  trend: undefined,
  measurements: [],
  lastSwept: [],
  windowLabel: 'last 28 days',
  relatedScopes: null,
  overrideRetired: null,
  recentRuns: null,
  recentRunsState: 'loading',
  measurementsState: 'loading',
  keptBy: null,
  keptByState: 'loading',
};

describe('routineBySlug', () => {
  it('answers null on the bare child route', () => {
    expect(routineBySlug(null, [ROUTINE])).toBeNull();
  });

  it('finds the routine the slug names', () => {
    expect(routineBySlug('weekly', [ROUTINE, OTHER_ROUTINE])).toBe(OTHER_ROUTINE);
  });

  it('never resolves a routine by its display name', () => {
    expect(routineBySlug('Weekly sweep', [ROUTINE, OTHER_ROUTINE])).toBeNull();
  });

  it('answers null for a slug no loaded routine carries', () => {
    expect(routineBySlug('gone', [ROUTINE])).toBeNull();
  });
});

describe('routineEffectiveGraph', () => {
  it('answers null with no routine', () => {
    expect(routineEffectiveGraph(null, [EFFECTIVE_GRAPH], false)).toBeNull();
  });

  it('answers null while the graph read is pending', () => {
    expect(routineEffectiveGraph(ROUTINE, [EFFECTIVE_GRAPH], true)).toBeNull();
  });

  it("resolves the effective mint of the routine's graph", () => {
    expect(routineEffectiveGraph(ROUTINE, [SUPERSEDED_GRAPH, EFFECTIVE_GRAPH], false)).toBe(EFFECTIVE_GRAPH);
  });

  it('answers null when the graph has no effective mint', () => {
    expect(routineEffectiveGraph(ROUTINE, [SUPERSEDED_GRAPH], false)).toBeNull();
  });
});

describe('routineOverrideRetired', () => {
  it('answers null with no routine', () => {
    expect(routineOverrideRetired(null, [{ routineId: 'rtn_1', revision: 2, retired: true }])).toBeNull();
  });

  it('answers null when nothing is pending for the routine', () => {
    expect(routineOverrideRetired(ROUTINE, [{ routineId: 'rtn_2', revision: 2, retired: true }])).toBeNull();
  });

  it("answers the pending mutation's retired flag", () => {
    expect(routineOverrideRetired(ROUTINE, [{ routineId: 'rtn_1', revision: 2, retired: true }])).toBe(true);
    expect(routineOverrideRetired(ROUTINE, [{ routineId: 'rtn_1', revision: 2, retired: false }])).toBe(false);
  });
});

describe('strategySteps', () => {
  it('answers no steps before the graph resolves', () => {
    expect(strategySteps(undefined)).toEqual([]);
  });

  it('answers no steps for a graph carrying no nodes', () => {
    expect(strategySteps({ ...GRAPH, nodes: [] })).toEqual([]);
  });

  it('maps each node to its name and prompt, null when it has none', () => {
    expect(strategySteps(GRAPH)).toEqual([
      { name: 'survey', prompt: 'Survey the repo.' },
      { name: 'report', prompt: null },
    ]);
  });
});

describe('measurementReadings', () => {
  it('answers no readings before the sweeps read resolves', () => {
    expect(measurementReadings(undefined)).toEqual([]);
  });

  it('maps each measurement', () => {
    expect(measurementReadings(SWEEPS)).toEqual([
      { scopeSlug: 'blizzard', producedAt: '2026-01-10T00:00:00Z', measurement: '3 findings' },
    ]);
  });
});

describe('lastSweptRows', () => {
  it('answers no rows before the sweeps read resolves', () => {
    expect(lastSweptRows(undefined)).toEqual([]);
  });

  it('renders revisions repo@rev in repo order, and a dash when there are none', () => {
    expect(lastSweptRows(SWEEPS)).toEqual([
      {
        scopeSlug: 'blizzard',
        findingSetId: 'fins_1',
        producedAt: '2026-01-10T00:00:00Z',
        revisionsLabel: 'blizzard@abc123, hub@def456',
      },
      { scopeSlug: 'never-swept', findingSetId: null, producedAt: null, revisionsLabel: '—' },
    ]);
  });
});

describe('relatedScopeRows', () => {
  it('answers null until the related-scopes read resolves', () => {
    expect(relatedScopeRows(undefined, ROUTINE)).toBeNull();
  });

  it('answers null with no routine', () => {
    expect(relatedScopeRows(['blizzard'], null)).toBeNull();
  });

  it("marks the routine's own default scope", () => {
    expect(relatedScopeRows(['blizzard', 'docs'], ROUTINE)).toEqual([
      { slug: 'blizzard', isDefault: true },
      { slug: 'docs', isDefault: false },
    ]);
  });
});

describe('proposalCountsRows', () => {
  it('answers no rows before the read resolves', () => {
    expect(proposalCountsRows(undefined)).toEqual([]);
  });

  it('maps each row', () => {
    expect(proposalCountsRows(PROPOSAL_COUNTS)).toEqual([
      {
        origin: 'routine-run',
        proposalClass: 'stale-docstring',
        created: 6,
        open: 2,
        passed: 1,
        acceptedWithItem: 3,
        acceptedWithoutItem: 0,
      },
    ]);
  });

  it('renders an origin this build does not know raw', () => {
    const rows = proposalCountsRows({ rows: [gardenProposalCountsRow({ origin: { unlisted: 'imported' } })] });
    expect(rows[0].origin).toBe('imported');
  });
});

describe('trendTotals', () => {
  it('sums every period of the window', () => {
    expect(trendTotals(TREND)).toEqual({ created: 6, outflow: 3, withdrawn: 1, reopened: 3 });
  });

  it('answers zeros for a window with no periods', () => {
    expect(trendTotals({ ...TREND, periods: [] })).toEqual({ created: 0, outflow: 0, withdrawn: 0, reopened: 0 });
  });
});

describe('keptByVms', () => {
  it('keeps nothing when the routine carries no keptBy', () => {
    expect(keptByVms(ROUTINE)).toEqual([]);
  });

  it('maps every keeping chore to a row, in the hub’s order', () => {
    const routine: Routine = {
      ...ROUTINE,
      keptBy: [
        gardenRoutineKeeper({ nextMoment: fixtureInstant(14 * 3600) }),
        gardenRoutineKeeper({ cadenceSlug: 'weekly', cadenceName: 'Weekly', choreKey: 'deep' }),
      ],
    };
    expect(keptByVms(routine)).toEqual([
      { cadenceSlug: 'evening', cadenceName: 'Evening', choreKey: 'nightly', nextMoment: fixtureInstant(14 * 3600) },
      { cadenceSlug: 'weekly', cadenceName: 'Weekly', choreKey: 'deep', nextMoment: null },
    ]);
  });
});

describe('routinePanelVm', () => {
  it('answers null with no routine selected', () => {
    expect(routinePanelVm(null, PARTS)).toBeNull();
  });

  it('composes the record and passes the parts through', () => {
    expect(routinePanelVm(ROUTINE, PARTS)).toEqual({
      record: {
        slug: 'nightly',
        name: 'Nightly sweep',
        graphName: 'garden-routine',
        defaultScopeSlug: 'blizzard',
        modelEffort: 'claude-sonnet-5 · medium',
      },
      blockedReason: null,
      strategy: PARTS.strategy,
      trend: null,
      measurements: [],
      lastSwept: [],
      windowLabel: 'last 28 days',
      relatedScopes: null,
      retired: false,
      renderedRetired: false,
      recentRuns: null,
      recentRunsState: 'loading',
      measurementsState: 'loading',
      runsLabel: null,
      keptBy: null,
      keptByState: 'loading',
    });
  });

  it('defaults an unset model and effort', () => {
    const vm = routinePanelVm({ ...ROUTINE, defaultModel: [], defaultEffort: null }, PARTS);
    expect(vm?.record.modelEffort).toBe('—');
  });

  it("names the routine's graph as the blocked reason", () => {
    expect(routinePanelVm(ROUTINE, { ...PARTS, blocked: true })?.blockedReason).toBe(
      'graph garden-routine has no effective mint',
    );
  });

  it('summarises a resolved trend', () => {
    expect(routinePanelVm(ROUTINE, { ...PARTS, trend: TREND })?.trend).toEqual(trendTotals(TREND));
  });

  it('renders the real retired flag with nothing pending', () => {
    const vm = routinePanelVm({ ...ROUTINE, retired: true }, PARTS);
    expect(vm?.retired).toBe(true);
    expect(vm?.renderedRetired).toBe(true);
  });

  it('renders a pending override over the real retired flag', () => {
    const vm = routinePanelVm(ROUTINE, { ...PARTS, overrideRetired: true });
    expect(vm?.retired).toBe(false);
    expect(vm?.renderedRetired).toBe(true);
  });
});

describe('routinePanelState', () => {
  it('rests empty on the bare child route, whatever the routine read says', () => {
    expect(routinePanelState(null, null, query({ pending: true }), false, false)).toBe('empty');
  });

  it('answers off the routine read while the named routine is unresolved', () => {
    expect(routinePanelState('nightly', null, query({ pending: true }), false, false)).toBe('loading');
    expect(routinePanelState('nightly', null, query({ error: true }), false, false)).toBe('error');
    expect(routinePanelState('gone', null, query({}), false, false)).toBe('empty');
  });

  it('waits on the graph read once the routine resolves', () => {
    expect(routinePanelState('nightly', ROUTINE, query({}), true, false)).toBe('loading');
    expect(routinePanelState('nightly', ROUTINE, query({}), false, true)).toBe('error');
  });

  it('is ready once the routine and the graph read resolve', () => {
    expect(routinePanelState('nightly', ROUTINE, query({}), false, false)).toBe('ready');
  });
});

describe('modelEffortLabel', () => {
  it('merges the model and effort into one fact', () => {
    expect(modelEffortLabel(['opus'], 'high')).toBe('opus · high');
    expect(modelEffortLabel(['opus', 'sonnet'], 'medium')).toBe('opus, sonnet · medium');
  });

  it('dashes a part the routine leaves unset, and reads a single dash when both are', () => {
    expect(modelEffortLabel([], 'high')).toBe('— · high');
    expect(modelEffortLabel(['opus'], null)).toBe('opus · —');
    expect(modelEffortLabel([], null)).toBe('—');
  });
});

function runRow(chunkId: string, routineSlug: string, mintedAt: string, added: number[]): RunRow {
  return gardenRunRow({
    chunkId,
    routineSlug,
    mintedAt,
    scopeSlug: 'hub-api',
    mode: 'delta',
    delivered: added.map((addedCount) => gardenDeliveredSet({ addedCount })),
  });
}

describe('routineRecentRuns', () => {
  const runs = [
    runRow('ch_1', 'nightly', '2026-03-01T00:00:00Z', [2]),
    runRow('ch_2', 'weekly', '2026-03-04T00:00:00Z', [9]),
    runRow('ch_3', 'nightly', '2026-03-05T00:00:00Z', [1, 3]),
  ];

  it("keeps the routine's own runs, newest first, counting the findings each added", () => {
    expect(routineRecentRuns(runs, 'nightly')).toEqual([
      { chunkId: 'ch_3', scopeSlug: 'hub-api', mode: 'delta', findings: 4, mintedAt: '2026-03-05T00:00:00Z' },
      { chunkId: 'ch_1', scopeSlug: 'hub-api', mode: 'delta', findings: 2, mintedAt: '2026-03-01T00:00:00Z' },
    ]);
  });

  it("matches runs by the routine's slug, never its display name", () => {
    expect(routineRecentRuns(runs, 'Nightly sweep')).toEqual([]);
  });

  it('answers null while the runs read is pending or no routine is selected', () => {
    expect(routineRecentRuns(undefined, 'nightly')).toBeNull();
    expect(routineRecentRuns(runs, null)).toBeNull();
  });

  it('counts the runs the window holds, labelled with the window', () => {
    expect(routineRunsLabel(routineRecentRuns(runs, 'nightly'), 'last 28 days')).toBe('2 in the last 28 days');
    expect(routineRunsLabel([], 'last 28 days')).toBe('0 in the last 28 days');
    expect(routineRunsLabel(null, 'last 28 days')).toBeNull();
  });
});

describe('whileEditing', () => {
  it('answers the value only while a baseline is held', () => {
    expect(whileEditing({ id: 'rtn_1' }, 'graph')).toBe('graph');
    expect(whileEditing(null, 'graph')).toBeNull();
  });
});
