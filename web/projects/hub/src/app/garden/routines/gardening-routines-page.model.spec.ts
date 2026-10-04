import type { GraphSummaryView, RoutineView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { presentRoutineName, routineListRows } from './gardening-routines-page.model';

const NIGHTLY: RoutineView = {
  routine_id: 'rtn_1',
  name: 'nightly',
  graph_name: 'garden-routine',
  default_scope_slug: 'blizzard',
  created_at: '2026-01-01T00:00:00Z',
};

const ORPHAN: RoutineView = { ...NIGHTLY, routine_id: 'rtn_2', name: 'orphan', graph_name: 'unminted', retired: true };

const EFFECTIVE_GRAPH: GraphSummaryView = {
  graph_id: 'gr_1',
  name: 'garden-routine',
  entry_node_id: 'nd_1',
  created_at: '2026-01-01T00:00:00Z',
  effective: true,
};

describe('presentRoutineName', () => {
  it('answers null on the bare child route', () => {
    expect(presentRoutineName(null, [NIGHTLY])).toBeNull();
  });

  it('answers the name while it names a loaded routine', () => {
    expect(presentRoutineName('nightly', [NIGHTLY, ORPHAN])).toBe('nightly');
  });

  it('answers null for a name no loaded routine carries', () => {
    expect(presentRoutineName('gone', [NIGHTLY])).toBeNull();
  });
});

describe('routineListRows', () => {
  it('flags a routine blocked when its graph has no effective mint', () => {
    expect(routineListRows([NIGHTLY, ORPHAN], [EFFECTIVE_GRAPH], false)).toEqual([
      { routineId: 'rtn_1', name: 'nightly', graphName: 'garden-routine', blocked: false, retired: false },
      { routineId: 'rtn_2', name: 'orphan', graphName: 'unminted', blocked: true, retired: true },
    ]);
  });

  it('flags no routine blocked while the graph read is pending', () => {
    expect(routineListRows([ORPHAN], [], true).map((row) => row.blocked)).toEqual([false]);
  });

  it('answers no rows for no routines', () => {
    expect(routineListRows([], [EFFECTIVE_GRAPH], false)).toEqual([]);
  });
});
