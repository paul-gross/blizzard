import type { AsyncStateQuery, GardenProposalCountsView, GardenSweepsView, GraphSummaryView, GraphView, RoutineView, TrendView } from 'fleet';
import { describe, expect, it } from 'vitest';

import {
  lastSweptRows,
  measurementReadings,
  proposalCountsRows,
  relatedScopeRows,
  routineByName,
  routineEffectiveGraph,
  routineOverrideRetired,
  routinePanelState,
  routinePanelVm,
  strategySteps,
  trendTotals,
  type RoutinePanelParts,
} from './gardening-routine-detail.model';

const ROUTINE: RoutineView = {
  routine_id: 'rtn_1',
  name: 'nightly',
  graph_name: 'garden-routine',
  default_scope_slug: 'blizzard',
  default_model: ['claude-sonnet-5'],
  default_effort: 'medium',
  created_at: '2026-01-01T00:00:00Z',
};

const OTHER_ROUTINE: RoutineView = { ...ROUTINE, routine_id: 'rtn_2', name: 'weekly' };

const EFFECTIVE_GRAPH: GraphSummaryView = {
  graph_id: 'gr_1',
  name: 'garden-routine',
  entry_node_id: 'nd_1',
  created_at: '2026-01-01T00:00:00Z',
  effective: true,
};

const SUPERSEDED_GRAPH: GraphSummaryView = { ...EFFECTIVE_GRAPH, graph_id: 'gr_0', effective: false };

const GRAPH: GraphView = {
  graph_id: 'gr_1',
  name: 'garden-routine',
  entry_node_id: 'nd_1',
  enabled: true,
  nodes: [
    { node_id: 'nd_1', name: 'survey', executor: 'runner', session: 'fresh', judged_by: 'worker', prompt: 'Survey the repo.' },
    { node_id: 'nd_2', name: 'report', executor: 'runner', session: 'fresh', judged_by: 'worker' },
  ],
};

const SWEEPS: GardenSweepsView = {
  routine_name: 'nightly',
  since: '2026-01-01T00:00:00Z',
  until: '2026-01-29T00:00:00Z',
  last_swept: [
    {
      scope_slug: 'blizzard',
      finding_set_id: 'fins_1',
      produced_at: '2026-01-10T00:00:00Z',
      revisions: { hub: 'def456', blizzard: 'abc123' },
    },
    { scope_slug: 'never-swept', finding_set_id: null, produced_at: null, revisions: {} },
  ],
  measurements: [{ scope_slug: 'blizzard', produced_at: '2026-01-10T00:00:00Z', measurement: '3 findings' }],
};

const TREND: TrendView = {
  routine_name: 'nightly',
  since: '2026-01-01T00:00:00Z',
  until: '2026-01-29T00:00:00Z',
  period_days: 7,
  periods: [
    { period_start: '2026-01-01T00:00:00Z', period_end: '2026-01-08T00:00:00Z', created: 2, exits: {}, outflow: 1, withdrawn: 0, reopened: 3 },
    { period_start: '2026-01-08T00:00:00Z', period_end: '2026-01-15T00:00:00Z', created: 4, exits: {}, outflow: 2, withdrawn: 1, reopened: 0 },
  ],
  age: { boundary: '2026-01-01T00:00:00Z', recent: 2, older: 0, unattributed: 0 },
};

const PROPOSAL_COUNTS: GardenProposalCountsView = {
  since: '2026-01-01T00:00:00Z',
  until: '2026-01-29T00:00:00Z',
  routine: 'nightly',
  rows: [
    {
      origin: 'routine-run',
      routine_name: 'nightly',
      class: 'stale-docstring',
      open: 2,
      passed: 1,
      accepted_with_item: 3,
      accepted_without_item: 0,
      created: 6,
    },
  ],
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
};

describe('routineByName', () => {
  it('answers null on the bare child route', () => {
    expect(routineByName(null, [ROUTINE])).toBeNull();
  });

  it('finds the routine the name names', () => {
    expect(routineByName('weekly', [ROUTINE, OTHER_ROUTINE])).toBe(OTHER_ROUTINE);
  });

  it('answers null for a name no loaded routine carries', () => {
    expect(routineByName('gone', [ROUTINE])).toBeNull();
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
    expect(routineOverrideRetired(null, [{ routineId: 'rtn_1', retired: true }])).toBeNull();
  });

  it('answers null when nothing is pending for the routine', () => {
    expect(routineOverrideRetired(ROUTINE, [{ routineId: 'rtn_2', retired: true }])).toBeNull();
  });

  it("answers the pending mutation's retired flag", () => {
    expect(routineOverrideRetired(ROUTINE, [{ routineId: 'rtn_1', retired: true }])).toBe(true);
    expect(routineOverrideRetired(ROUTINE, [{ routineId: 'rtn_1', retired: false }])).toBe(false);
  });
});

describe('strategySteps', () => {
  it('answers no steps before the graph resolves', () => {
    expect(strategySteps(undefined)).toEqual([]);
  });

  it('answers no steps for a graph carrying no nodes', () => {
    expect(strategySteps({ ...GRAPH, nodes: undefined })).toEqual([]);
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
});

describe('trendTotals', () => {
  it('sums every period of the window', () => {
    expect(trendTotals(TREND)).toEqual({ created: 6, outflow: 3, withdrawn: 1, reopened: 3 });
  });

  it('answers zeros for a window with no periods', () => {
    expect(trendTotals({ ...TREND, periods: [] })).toEqual({ created: 0, outflow: 0, withdrawn: 0, reopened: 0 });
  });
});

describe('routinePanelVm', () => {
  it('answers null with no routine selected', () => {
    expect(routinePanelVm(null, PARTS)).toBeNull();
  });

  it('composes the record and passes the parts through', () => {
    expect(routinePanelVm(ROUTINE, PARTS)).toEqual({
      record: {
        name: 'nightly',
        graphName: 'garden-routine',
        defaultScopeSlug: 'blizzard',
        defaultModel: ['claude-sonnet-5'],
        defaultEffort: 'medium',
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
    });
  });

  it('defaults an unset model and effort', () => {
    const vm = routinePanelVm({ ...ROUTINE, default_model: undefined, default_effort: undefined }, PARTS);
    expect(vm?.record.defaultModel).toEqual([]);
    expect(vm?.record.defaultEffort).toBeNull();
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
