import { formatWhen } from './when';

// A fixed local "now" — mid-afternoon so same-day boundaries sit inside one date.
const NOW = new Date(2026, 6, 18, 15, 30);

describe('formatWhen', () => {
  it('renders a same-day instant as the bare local HH:MM, zero-padded', () => {
    expect(formatWhen(new Date(2026, 6, 18, 9, 5).toISOString(), NOW)).toBe('09:05');
    expect(formatWhen(new Date(2026, 6, 18, 0, 0).toISOString(), NOW)).toBe('00:00');
  });

  it('renders yesterday with the Yesterday prefix', () => {
    expect(formatWhen(new Date(2026, 6, 17, 23, 59).toISOString(), NOW)).toBe('Yesterday 23:59');
  });

  it('renders anything older as the date alone', () => {
    expect(formatWhen(new Date(2026, 6, 16, 12, 0).toISOString(), NOW)).toBe('2026/07/16');
    expect(formatWhen(new Date(2025, 11, 31, 12, 0).toISOString(), NOW)).toBe('2025/12/31');
  });

  it('treats a slightly-future stamp (clock skew) as today, not a date', () => {
    expect(formatWhen(new Date(2026, 6, 18, 23, 1).toISOString(), NOW)).toBe('23:01');
    expect(formatWhen(new Date(2026, 6, 19, 0, 30).toISOString(), NOW)).toBe('00:30');
  });

  it('returns an empty string for an unparseable input', () => {
    expect(formatWhen('not-a-date', NOW)).toBe('');
    expect(formatWhen('', NOW)).toBe('');
  });
});
