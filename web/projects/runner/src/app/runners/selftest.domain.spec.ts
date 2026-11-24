import { selftestStatusLabel } from './selftest.domain';

describe('selftestStatusLabel', () => {
  it('reads a known status as itself and an unknown one as sent', () => {
    expect(selftestStatusLabel('passed')).toBe('passed');
    expect(selftestStatusLabel({ kind: 'unknown', raw: 'queued' })).toBe('queued');
  });
});
