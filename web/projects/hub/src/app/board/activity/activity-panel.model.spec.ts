import { type ActivityView, type AsyncStateQuery, hubApi, type LoggedEvent } from 'fleet';
import { describe, expect, it } from 'vitest';

import { activityPanelState, activityRows, backfillEvents, mergeActivityFeeds } from './activity-panel.model';

const { HubEventType } = hubApi;

const event = (seq: number, at: number, fields: Partial<LoggedEvent> = {}): LoggedEvent => ({
  seq,
  type: HubEventType.QUEUE_CHANGED,
  data: {},
  at,
  ...fields,
});

const query = (fields: Partial<Record<keyof AsyncStateQuery, boolean>>): AsyncStateQuery => ({
  isPending: () => fields.isPending ?? false,
  isError: () => fields.isError ?? false,
});

describe('backfillEvents', () => {
  it('is empty before the read resolves', () => {
    expect(backfillEvents(undefined)).toEqual([]);
  });

  it('shapes each row into a logged event with a distinct negative seq and an epoch `at`', () => {
    const rows = [
      { at: '2026-06-01T12:00:00Z', type: HubEventType.QUEUE_CHANGED, key: 'k1' },
      { at: '2026-06-01T11:00:00Z', type: HubEventType.QUESTION_ASKED, key: 'k2', chunk_id: 'ch_1' },
    ] as ActivityView[];
    expect(backfillEvents(rows)).toEqual([
      { seq: -1, type: HubEventType.QUEUE_CHANGED, data: { key: 'k1' }, at: Date.parse('2026-06-01T12:00:00Z'), key: 'k1' },
      {
        seq: -2,
        type: HubEventType.QUESTION_ASKED,
        data: { key: 'k2', chunk_id: 'ch_1' },
        at: Date.parse('2026-06-01T11:00:00Z'),
        key: 'k2',
      },
    ]);
  });
});

describe('mergeActivityFeeds', () => {
  it('drops a backfilled row whose key a live frame also names, keeping keyless rows', () => {
    const backfill = [event(-1, 10, { key: 'dup' }), event(-2, 5), event(-3, 7, { key: 'only-backfill' })];
    const live = [event(1, 20, { key: 'dup' })];
    expect(mergeActivityFeeds(backfill, live, 10).map((e) => e.seq)).toEqual([-2, -3, 1]);
  });

  it('keeps the backfill row\'s time on a replayed live frame, and a live-only frame\'s own time', () => {
    const backfill = [event(-1, 10, { key: 'dup' })];
    const live = [event(1, 500, { key: 'dup' }), event(2, 600, { key: 'fresh' })];
    expect(mergeActivityFeeds(backfill, live, 10).map((e) => [e.seq, e.at])).toEqual([
      [1, 10],
      [2, 600],
    ]);
  });

  it('sorts oldest to newest on `at` and keeps only the newest `limit`', () => {
    const backfill = [event(-1, 30), event(-2, 10)];
    const live = [event(1, 20), event(2, 40)];
    expect(mergeActivityFeeds(backfill, live, 3).map((e) => e.seq)).toEqual([1, -1, 2]);
  });
});

describe('activityRows', () => {
  it('renders newest first with a summary per event type', () => {
    const rows = activityRows([
      event(1, 0, { type: HubEventType.QUESTION_ASKED, data: { chunk_id: 'ch_1' } }),
      event(2, 0, { type: 'some-new-type' }),
    ]);
    expect(rows.map((r) => [r.seq, r.type])).toEqual([
      [2, 'some-new-type'],
      [1, HubEventType.QUESTION_ASKED],
    ]);
    expect(rows[0].message).toBe('some-new-type');
    expect(rows[1].message).toMatch(/ asked a question$/);
    expect(rows.every((r) => typeof r.time === 'string')).toBe(true);
  });

  it('phrases a runner change, falling back to the raw kind', () => {
    const [paused, unrecognized] = activityRows([
      event(1, 0, { type: HubEventType.RUNNER_CHANGED, data: { runner_id: 'r1', kind: 'mystery' } }),
      event(2, 0, { type: HubEventType.RUNNER_CHANGED, data: { runner_id: 'r1', kind: 'paused', by: 'op', reason: 'why' } }),
    ]);
    expect(paused.message).toMatch(/^runner \S+ paused by op — why$/);
    expect(unrecognized.message).toMatch(/^runner \S+ mystery$/);
  });

  it('names a runner by display name, a runner with no name by its compact id', () => {
    const named = { runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3', runner_name: 'r-claude' };
    const [logged, nameless, added] = activityRows([
      event(1, 0, { type: HubEventType.RUNNER_CHANGED, data: { ...named, kind: 'added', by: 'alice' } }),
      event(2, 0, { type: HubEventType.RUNNER_CHANGED, data: { runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYN7Q2M', kind: 'paused', by: 'op' } }),
      event(3, 0, { type: HubEventType.EVENT_LOGGED, data: { ...named, severity: 'warning', kind: 'attempt-failed' } }),
    ]);
    expect(added.message).toBe('runner R-ABF3.r-claude added to the fleet by alice');
    expect(nameless.message).toBe('runner R-7Q2M paused by op');
    expect(logged.message).toBe('R-ABF3.r-claude · warning attempt-failed');
  });
});

describe('activityPanelState', () => {
  it('reads an SSE auth failure as error whatever the backfill holds', () => {
    expect(activityPanelState(true, query({}), false)).toBe('error');
  });

  it('otherwise follows the backfill query', () => {
    expect(activityPanelState(false, query({ isPending: true }), true)).toBe('loading');
    expect(activityPanelState(false, query({}), true)).toBe('empty');
    expect(activityPanelState(false, query({}), false)).toBe('ready');
  });
});
