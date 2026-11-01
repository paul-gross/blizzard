import { verdictTone } from './verdict-tone';

describe('verdictTone', () => {
  it.each([
    ['fail', 'needs'],
    ['conflict', 'needs'],
    ['needs-human', 'needs'],
    ['bounced', 'needs'],
    ['run', 'spawning'],
    ['migrated', 'spawning'],
    ['waiting', 'waiting'],
    ['paused', 'waiting'],
    ['restarted', 'waiting'],
  ] as const)('reads %s as %s', (choice, tone) => {
    expect(verdictTone(choice)).toBe(tone);
  });

  it('reads running for any other choice, or none', () => {
    expect(verdictTone('pass')).toBe('running');
    expect(verdictTone('toString')).toBe('running');
    expect(verdictTone(null)).toBe('running');
  });
});
