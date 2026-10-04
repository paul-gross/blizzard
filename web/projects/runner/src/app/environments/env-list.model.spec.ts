import { compactRef, type runnerApi } from 'fleet';

import { envRows, heldForLabel } from './env-list.model';

const NOW = Date.parse('2026-07-16T12:00:00.000Z');

describe('heldForLabel', () => {
  it('renders blank for an unheld environment', () => {
    expect(heldForLabel(null, NOW)).toBe('');
    expect(heldForLabel(undefined, NOW)).toBe('');
  });

  it('renders the held-for duration since the binding fact', () => {
    expect(heldForLabel('2026-07-16T11:18:00.000Z', NOW)).toBe('42m');
  });

  it('renders an em dash for a skew-broken timestamp', () => {
    expect(heldForLabel('2026-07-16T13:00:00.000Z', NOW)).toBe('—');
    expect(heldForLabel('not-a-timestamp', NOW)).toBe('—');
  });
});

describe('envRows', () => {
  it('maps a held and an unheld environment in wire order', () => {
    const envs: runnerApi.EnvironmentView[] = [
      { environment_id: 'e1', chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9', held_since: '2026-07-16T11:59:18.000Z' },
      { environment_id: 'e2', chunk_id: null, held_since: null },
    ];
    expect(envRows(envs, NOW)).toEqual([
      { environmentId: 'e1', isHeld: true, chunkRef: compactRef('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9'), heldFor: '42s' },
      { environmentId: 'e2', isHeld: false, chunkRef: '', heldFor: '' },
    ]);
  });

  it('treats an absent chunk_id as unheld', () => {
    expect(envRows([{ environment_id: 'e3' }], NOW)).toEqual([
      { environmentId: 'e3', isHeld: false, chunkRef: '', heldFor: '' },
    ]);
  });

  it('maps an empty pool to no rows', () => {
    expect(envRows([], NOW)).toEqual([]);
  });
});
