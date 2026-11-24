import { hostRunner } from '../core/host-runner.builders';
import { settingRow, settingsRecord } from './settings.builders';
import {
  inForceLine,
  lifetimeBadge,
  settingGroups,
  settingRowOf,
  settingsChanges,
  settingsRecords,
  whileEditing,
} from './settings.model';

describe('settings model', () => {
  it('lists the host, then each runner with an id, the Runner filter`s alone when it names one', () => {
    const runners = [
      hostRunner({ runner_id: 'rn_1', name: 'r-claude' }),
      hostRunner({ runner_id: 'rn_2', name: 'r-old', state: 'retired' }),
      hostRunner({ runner_id: null, name: 'r-broken' }),
    ];

    expect(settingsRecords(runners, null)).toEqual([
      { id: 'host', title: 'Host', target: { kind: 'host' }, retired: false },
      { id: 'rn_1', title: 'r-claude', target: { kind: 'runner', runnerId: 'rn_1' }, retired: false },
      { id: 'rn_2', title: 'r-old', target: { kind: 'runner', runnerId: 'rn_2' }, retired: true },
    ]);
    expect(settingsRecords(runners, 'r-old').map((r) => r.id)).toEqual(['host', 'rn_2']);
  });

  it('badges each lifetime, an unknown one raw and idle', () => {
    expect(lifetimeBadge('pass')).toEqual({ label: 'pass', tone: 'running' });
    expect(lifetimeBadge('session')).toEqual({ label: 'session', tone: 'waiting' });
    expect(lifetimeBadge('immutable')).toEqual({ label: 'immutable', tone: 'idle' });
    expect(lifetimeBadge({ kind: 'unknown', raw: 'tick' })).toEqual({ label: 'tick', tone: 'idle' });
  });

  it('groups keys by table, top-level first, each editable by the domain`s rule', () => {
    const record = settingsRecord({
      settings: [
        settingRow({ key: 'throttle.budget.usd', value: 25, source: 'entry' }),
        settingRow({ key: 'name', value: 'r-claude', lifetime: 'immutable' }),
        settingRow({ key: 'throttle.budget.window_hours', value: 24 }),
        settingRow({ key: 'runner_prompt_file', value: null, lifetime: 'session', source: { kind: 'unknown', raw: 'file' } }),
      ],
    });

    const groups = settingGroups(record, true, false);

    expect(groups.map((g) => [g.table, g.rows.map((r) => [r.name, r.value, r.source, r.editable])])).toEqual([
      ['', [['name', 'r-claude', 'default', false], ['runner_prompt_file', 'null', 'file', true]]],
      ['throttle.budget', [['usd', '25', 'entry', true], ['window_hours', '24', 'default', true]]],
    ]);
    expect(settingGroups(record, false, false).flatMap((g) => g.rows).some((r) => r.editable)).toBe(false);
    expect(settingGroups(record, true, true).flatMap((g) => g.rows).some((r) => r.editable)).toBe(false);
    expect(settingGroups(undefined, true, false)).toEqual([]);
  });

  it('reads the in_force line: in force, pending, failed, and never yet in force', () => {
    expect(inForceLine(undefined)).toBeNull();
    expect(inForceLine(settingsRecord())).toEqual({ running: 3, since: '2026-11-20T21:14:00Z', pending: null, failed: null });
    expect(inForceLine(settingsRecord({ revision: 4 }))?.pending).toBe(4);

    const failed = { revision: 4, error: 'no such file', since: '2026-11-20T21:20:00Z' };
    expect(inForceLine(settingsRecord({ revision: 4, inForce: { revision: 3, since: null, failed } }))).toEqual({
      running: 3,
      since: null,
      pending: null,
      failed,
    });
    expect(inForceLine(settingsRecord({ revision: 5, inForce: { revision: 3, since: null, failed } }))?.pending).toBe(5);
    expect(inForceLine(settingsRecord({ revision: 2, inForce: { revision: null, since: null, failed: null } }))?.pending).toBe(2);
    expect(inForceLine(settingsRecord({ revision: 0, inForce: { revision: null, since: null, failed: null } }))?.pending).toBeNull();
  });

  it('finds the row an edit names', () => {
    const record = settingsRecord();
    expect(settingRowOf(record, 'throttle.budget.usd')?.key).toBe('throttle.budget.usd');
    expect(settingRowOf(record, 'gone')).toBeNull();
    expect(settingRowOf(record, null)).toBeNull();
  });

  it('flattens change pages, naming a host row`s record unless it is the host`s own', () => {
    const base = { recordedAt: 't', revision: 1, op: 'edit', door: 'api', actor: 'a', record: null } as const;
    const rows = settingsChanges([
      {
        changes: [
          { ...base, id: 2, diff: [{ key: 'k', old: null, new: { value: 3, atDefault: false } }] },
          { ...base, id: 1, op: { kind: 'unknown', raw: 'mint' }, record: { kind: 'workspace', key: 'ws-a' }, diff: [] },
        ],
        nextBefore: 1,
      },
      { changes: [{ ...base, id: 0, record: { kind: 'host', key: null }, diff: [{ key: 'k', old: { value: 1, atDefault: true }, new: { value: 'x', atDefault: false } }] }], nextBefore: null },
    ]);

    expect(rows.map((r) => [r.id, r.op, r.record, r.diff])).toEqual([
      [2, 'edit', null, [{ key: 'k', old: '—', new: '3' }]],
      [1, 'mint', 'workspace ws-a', []],
      [0, 'edit', null, [{ key: 'k', old: '1 (default)', new: 'x' }]],
    ]);
    expect(settingsChanges(undefined)).toEqual([]);
  });

  it('holds the form only while a baseline is held', () => {
    expect(whileEditing(null, 'tree')).toBeNull();
    expect(whileEditing({}, 'tree')).toBe('tree');
  });
});
