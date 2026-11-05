import type { AsyncStateQuery, GraphSummaryView, RoutineView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { presentRoutineSlug, routineListRows } from './gardening-routines-page.model';

const NIGHTLY: RoutineView = {
  routine_id: 'rtn_1',
  slug: 'nightly',
  name: 'Nightly sweep',
  graph_name: 'garden-routine',
  default_scope_slug: 'blizzard',
  created_at: '2026-01-01T00:00:00Z',
  default_model: [],
  default_effort: null,
  default_harnesses: [],
  project_id: null,
  project_name: null,
  project_slug: null,
  retired: false,
  revision: null,
};

const ORPHAN: RoutineView = { ...NIGHTLY, routine_id: 'rtn_2', slug: 'orphan', name: 'Orphan', graph_name: 'unminted', retired: true };

const EFFECTIVE_GRAPH: GraphSummaryView = {
  graph_id: 'gr_1',
  name: 'garden-routine',
  entry_node_id: 'nd_1',
  created_at: '2026-01-01T00:00:00Z',
  effective: true,
  lifecycle: 'effective',
  retired: false,
};

describe('presentRoutineSlug', () => {
  it('answers null on the bare child route', () => {
    expect(presentRoutineSlug(null, [NIGHTLY])).toBeNull();
  });

  it('answers the slug while it names a loaded routine', () => {
    expect(presentRoutineSlug('nightly', [NIGHTLY, ORPHAN])).toBe('nightly');
  });

  it('answers null for a display name, which never keys a routine', () => {
    expect(presentRoutineSlug('Nightly sweep', [NIGHTLY])).toBeNull();
  });

  it('answers null for a slug no loaded routine carries', () => {
    expect(presentRoutineSlug('gone', [NIGHTLY])).toBeNull();
  });
});

const query = (state: 'pending' | 'error' | 'success'): AsyncStateQuery => ({
  isPending: () => state === 'pending',
  isError: () => state === 'error',
});

describe('routineListRows', () => {
  it('flags a routine blocked when its graph has no effective mint', () => {
    expect(routineListRows([NIGHTLY, ORPHAN], [EFFECTIVE_GRAPH], query('success'))).toEqual([
      { routineId: 'rtn_1', slug: 'nightly', name: 'Nightly sweep', graphName: 'garden-routine', blocked: false, retired: false },
      { routineId: 'rtn_2', slug: 'orphan', name: 'Orphan', graphName: 'unminted', blocked: true, retired: true },
    ]);
  });

  it('flags no routine blocked while the graph read is pending', () => {
    expect(routineListRows([ORPHAN], [], query('pending')).map((row) => row.blocked)).toEqual([false]);
  });

  it('flags no routine blocked when the graph read failed', () => {
    expect(routineListRows([ORPHAN], [], query('error')).map((row) => row.blocked)).toEqual([false]);
  });

  it('answers no rows for no routines', () => {
    expect(routineListRows([], [EFFECTIVE_GRAPH], query('success'))).toEqual([]);
  });
});
