import { type runnerApi } from 'fleet';
import { describe, expect, it } from 'vitest';

import { headerConnectionLabel, headerStatCells } from './app-header.model';

describe('headerConnectionLabel', () => {
  it('reads a reconnecting stream first, whatever else holds', () => {
    expect(headerConnectionLabel('reconnecting', true, true, true)).toBe('reconnecting…');
  });

  it('reads a settled stream auth failure as degraded, over the dashboard read', () => {
    expect(headerConnectionLabel('closed', true, true, true)).toBe('degraded');
  });

  it("falls through to the dashboard read's own state", () => {
    expect(headerConnectionLabel('open', false, true, false)).toBe('connecting…');
    expect(headerConnectionLabel('open', false, false, true)).toBe('offline');
    expect(headerConnectionLabel('open', false, false, false)).toBe('ok');
  });
});

describe('headerStatCells', () => {
  it('withholds every cell while the dashboard read is pending', () => {
    expect(headerStatCells(true, undefined)).toEqual([]);
  });

  it('counts environments holding a chunk over the pool, and agents over max_agents', () => {
    const dashboard = {
      environments: { items: [{ chunk_id: 'ch_1' }, { chunk_id: null }, {}] },
      runner: { capacities: { used: 2, max_agents: 4 } },
    } as unknown as runnerApi.DashboardView;
    expect(headerStatCells(false, dashboard)).toEqual([
      { key: 'envs', label: 'Envs', value: 1, capacity: 3 },
      { key: 'agents', label: 'Agents', value: 2, capacity: 4 },
    ]);
  });

  it('reads zeroes off a resolved but empty body', () => {
    expect(headerStatCells(false, undefined)).toEqual([
      { key: 'envs', label: 'Envs', value: 0, capacity: 0 },
      { key: 'agents', label: 'Agents', value: 0, capacity: 0 },
    ]);
  });
});
