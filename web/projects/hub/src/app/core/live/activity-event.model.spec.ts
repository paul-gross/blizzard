import { HubEventKind } from 'fleet/shell';
import { isActivityEvent } from './activity-event.model';

describe('isActivityEvent', () => {
  it('classifies durable facts independently of invalidation', () => {
    expect(isActivityEvent(HubEventKind.CHUNK_CHANGED, { cause: 'claimed' })).toBe(true);
    expect(isActivityEvent(HubEventKind.CHUNK_CHANGED, { cause: 'future' })).toBe(false);
    expect(isActivityEvent(HubEventKind.RUNNER_CHANGED, { kind: 'paused' })).toBe(true);
    expect(isActivityEvent(HubEventKind.RUNNER_CHANGED, { kind: 'heartbeat' })).toBe(false);
    expect(isActivityEvent(HubEventKind.EVENT_LOGGED, {})).toBe(true);
    expect(isActivityEvent(HubEventKind.QUESTION_ASKED, {})).toBe(false);
    expect(isActivityEvent({ kind: 'unknown', raw: 'future' }, {})).toBe(false);
  });
});
