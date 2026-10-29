import { expect, test } from '@playwright/test';
import { daemonEnv } from '../env';

test('an ambient BLIZZARD_CRASH_POINT never reaches a daemon, while an explicit one does', () => {
  process.env.BLIZZARD_CRASH_POINT = 'ambient-point';
  try {
    expect(daemonEnv().BLIZZARD_CRASH_POINT).toBeUndefined();
    expect(daemonEnv({ BLIZZARD_CRASH_POINT: 'explicit-point' }).BLIZZARD_CRASH_POINT).toBe('explicit-point');
  } finally {
    delete process.env.BLIZZARD_CRASH_POINT;
  }
});
