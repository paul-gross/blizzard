import type { HostRecord } from './host-records.domain';
import { hostRecordOf, hostRecordVms, recordStateBadge, recordVerbs, retirePrompt, whileCreating } from './host-records.model';

const record = (overrides: Partial<HostRecord> = {}): HostRecord => ({
  kind: 'workspace',
  key: 'web',
  revision: 2,
  state: 'active',
  settings: {},
  ...overrides,
});

describe('host records model', () => {
  it('badges each state, an unknown one raw and idle', () => {
    expect(recordStateBadge('active')).toEqual({ label: 'active', tone: 'running' });
    expect(recordStateBadge('retired')).toEqual({ label: 'retired', tone: 'idle' });
    expect(recordStateBadge({ kind: 'unknown', raw: 'paused' })).toEqual({ label: 'paused', tone: 'idle' });
  });

  it('offers verbs only to the superuser, edit only of a kept record, retire of an active and enable of a retired', () => {
    expect(recordVerbs(record(), false)).toEqual({ edit: false, retire: false, enable: false });
    expect(recordVerbs(record(), true)).toEqual({ edit: true, retire: true, enable: false });
    expect(recordVerbs(record({ state: 'retired' }), true)).toEqual({ edit: true, retire: false, enable: true });
    expect(recordVerbs(record({ revision: null }), true)).toEqual({ edit: false, retire: false, enable: false });
    expect(recordVerbs(record({ state: { kind: 'unknown', raw: 'paused' } }), true)).toEqual({ edit: true, retire: false, enable: false });
  });

  it('lists active records first, each group in key order, their stated keys sorted', () => {
    const rows = hostRecordVms(
      [record({ key: 'b', state: 'retired' }), record({ key: 'c', settings: { root: '/r', envs: ['x'] } }), record({ key: 'a' })],
      true,
    );
    expect(rows.map((r) => [r.key, r.retired])).toEqual([
      ['a', false],
      ['c', false],
      ['b', true],
    ]);
    expect(rows[1].settings).toEqual([
      { key: 'envs', value: '["x"]' },
      { key: 'root', value: '/r' },
    ]);
    expect(hostRecordVms(undefined, true)).toEqual([]);
  });

  it('finds the open record, and none once it is gone', () => {
    const records = [record({ key: 'a' })];
    expect(hostRecordOf(records, 'a')?.key).toBe('a');
    expect(hostRecordOf(records, 'gone')).toBeNull();
    expect(hostRecordOf(records, null)).toBeNull();
  });

  it('asks before retiring, naming the record', () => {
    expect(retirePrompt('subscription', 'max')).toMatchObject({ heading: 'Retire subscription', confirmLabel: 'Retire', variant: 'danger' });
    expect(retirePrompt('subscription', 'max').message).toContain('subscription max');
  });

  it('holds the create form only while a create is open', () => {
    expect(whileCreating(false, 'tree')).toBeNull();
    expect(whileCreating(true, 'tree')).toBe('tree');
  });
});
