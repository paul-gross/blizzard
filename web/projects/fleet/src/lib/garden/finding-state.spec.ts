import { findingStateTone } from './finding-state';

describe('findingStateTone', () => {
  /** A still-open finding reads amber (`live`) or amber-hi (`gone`, waiting on a person
   * to confirm); `delivered` reads `takeover`, a delivery's claim not yet confirmed by
   * the routine; an outflow exit reads green, the ground having moved; a withdrawn exit
   * reads dim, since only a judgment changed. */
  it('tones each state by open, delivered, outflow, or withdrawn', () => {
    expect(findingStateTone('live')).toBe('running');
    expect(findingStateTone('gone')).toBe('waiting');
    expect(findingStateTone('delivered')).toBe('takeover');
    expect(findingStateTone('resolved')).toBe('done');
    expect(findingStateTone('gone-confirmed')).toBe('done');
    expect(findingStateTone('wont-fix')).toBe('idle');
    expect(findingStateTone('not-a-finding')).toBe('idle');
    expect(findingStateTone('superseded')).toBe('idle');
  });

  it('falls back to idle for a state this build does not know', () => {
    expect(findingStateTone('some-future-state')).toBe('idle');
  });
});
