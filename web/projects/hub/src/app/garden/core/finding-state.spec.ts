import { findingStateTone } from './finding-state';

describe('findingStateTone', () => {
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
