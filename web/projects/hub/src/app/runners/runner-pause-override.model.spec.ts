import { describe, expect, it } from 'vitest';

import { pendingRunnerIds, withPendingRunnerPauses } from './runner-pause-override.model';

const row = (runnerId: string, hubPaused: boolean) => ({ runner_id: runnerId, hub_paused: hubPaused, label: runnerId });

describe('pendingRunnerIds', () => {
  it('lists each in-flight runner id', () => {
    expect(
      pendingRunnerIds([
        { runnerId: 'r1', paused: true },
        { runnerId: 'r2', paused: false },
      ]),
    ).toEqual(['r1', 'r2']);
  });

  it('is empty with nothing in flight', () => {
    expect(pendingRunnerIds([])).toEqual([]);
  });
});

describe('withPendingRunnerPauses', () => {
  const rows = [row('r1', false), row('r2', true), row('r3', false)];

  it('returns the rows themselves with nothing in flight', () => {
    expect(withPendingRunnerPauses(rows, [])).toBe(rows);
  });

  it('folds each requested brake onto its own row only', () => {
    const result = withPendingRunnerPauses(rows, [
      { runnerId: 'r1', paused: true },
      { runnerId: 'r2', paused: false },
    ]);
    expect(result.map((r) => [r.runner_id, r.hub_paused])).toEqual([
      ['r1', true],
      ['r2', false],
      ['r3', false],
    ]);
    expect(result[0].label).toBe('r1');
    expect(result[2]).toBe(rows[2]);
  });

  it('ignores a request for a runner not in the rows', () => {
    const result = withPendingRunnerPauses(rows, [{ runnerId: 'r9', paused: true }]);
    expect(result).toEqual(rows);
  });
});
