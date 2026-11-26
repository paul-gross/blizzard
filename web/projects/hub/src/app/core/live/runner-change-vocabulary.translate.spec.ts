import { hubApi } from 'fleet/hub-api';

describe('runner change wire vocabulary', () => {
  it('pins the activity and stream kinds exercised by the live update spec', () => {
    expect(Object.values(hubApi.ActivityRunnerChangeKind)).toEqual([
      'paused', 'resumed', 'locally-paused', 'locally-resumed',
    ]);
    expect(Object.values(hubApi.RunnerChangeKind)).toEqual([
      'paused', 'resumed', 'locally-paused', 'locally-resumed', 'added', 'registered', 'heartbeat',
      'external-usage', 'retired', 'reinstated', 'token-revoked',
    ]);
  });
});
