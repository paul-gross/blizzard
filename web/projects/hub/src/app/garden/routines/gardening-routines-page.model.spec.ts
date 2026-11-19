import type { AsyncStateQuery, GraphSummaryView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { gardenRoutine, gardenRoutineKeeper } from '../core/routine.builders';
import type { Routine } from '../core/routine.domain';
import { presentRoutineSlug, routineListRows } from './gardening-routines-page.model';

const NIGHTLY: Routine = gardenRoutine({
  id: 'rtn_1',
  slug: 'nightly',
  name: 'Nightly sweep',
  graph: 'garden-routine',
  revision: null,
});

const ORPHAN: Routine = { ...NIGHTLY, id: 'rtn_2', slug: 'orphan', name: 'Orphan', graph: 'unminted', retired: true };

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
      { routineId: 'rtn_1', slug: 'nightly', name: 'Nightly sweep', graphName: 'garden-routine', blocked: false, retired: false, kept: false },
      { routineId: 'rtn_2', slug: 'orphan', name: 'Orphan', graphName: 'unminted', blocked: true, retired: true, kept: false },
    ]);
  });

  it('flags no routine blocked while the graph read is pending', () => {
    expect(routineListRows([ORPHAN], [], query('pending')).map((row) => row.blocked)).toEqual([false]);
  });

  it('flags no routine blocked when the graph read failed', () => {
    expect(routineListRows([ORPHAN], [], query('error')).map((row) => row.blocked)).toEqual([false]);
  });

  it('marks a routine kept when its keptBy is non-empty, and not when it is empty', () => {
    const kept: Routine = { ...NIGHTLY, keptBy: [gardenRoutineKeeper()] };
    expect(routineListRows([kept, ORPHAN], [EFFECTIVE_GRAPH], query('success')).map((r) => r.kept)).toEqual([true, false]);
  });

  it('answers no rows for no routines', () => {
    expect(routineListRows([], [EFFECTIVE_GRAPH], query('success'))).toEqual([]);
  });
});
