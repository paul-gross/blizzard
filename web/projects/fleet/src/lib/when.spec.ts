import { formatAge, formatClockTime, formatHeldFor, formatSeenAgo, formatUtcClock, formatWhen, ageMs } from './when';

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

describe('formatAge', () => {
  it('renders sub-minute deltas as -Ns', () => {
    expect(formatAge(0)).toBe('-0s');
    expect(formatAge(34_000)).toBe('-34s');
  });

  it('renders sub-hour deltas as -Nm', () => {
    expect(formatAge(12 * 60_000)).toBe('-12m');
  });

  it('renders hour-scale deltas as -HhMMm', () => {
    expect(formatAge((60 + 4) * 60_000)).toBe('-1h04m');
  });

  it('floors a negative delta at -0s rather than going negative', () => {
    expect(formatAge(-5_000)).toBe('-0s');
  });
});

describe('formatHeldFor', () => {
  it('renders sub-minute/sub-hour/hour-scale/day-scale deltas', () => {
    expect(formatHeldFor(42_000)).toBe('42s');
    expect(formatHeldFor(42 * 60_000)).toBe('42m');
    expect(formatHeldFor((60 + 4) * 60_000)).toBe('1h04m');
    expect(formatHeldFor(3 * 86_400_000)).toBe('3d');
  });

  it('floors a negative delta at 0s', () => {
    expect(formatHeldFor(-5_000)).toBe('0s');
  });
});

describe('ageMs (bzh:utc-instants)', () => {
  const REF = Date.parse('2026-07-16T12:00:00.000Z');

  it('returns null for an absent or unparseable instant', () => {
    expect(ageMs(null, REF)).toBeNull();
    expect(ageMs(undefined, REF)).toBeNull();
    expect(ageMs('not-a-date', REF)).toBeNull();
  });

  it('floors a small negative delta (benign skew) at zero', () => {
    expect(ageMs('2026-07-16T12:00:30.000Z', REF)).toBe(0);
  });

  it('returns null past the skew tolerance rather than a confident age', () => {
    expect(ageMs('2026-07-16T15:00:00.000Z', REF)).toBeNull();
  });

  it('returns the raw positive delta for a past instant', () => {
    expect(ageMs('2026-07-16T11:59:26.000Z', REF)).toBe(34_000);
  });
});

describe('formatSeenAgo (bzh:utc-instants)', () => {
  const REF = Date.parse('2026-07-16T12:00:00.000Z');

  it('reads a fresh heartbeat as "seen Ns ago"', () => {
    expect(formatSeenAgo('2026-07-16T11:59:55.000Z', true, REF)).toBe('seen 5s ago');
  });

  it('reads minute- and hour-scale deltas', () => {
    expect(formatSeenAgo('2026-07-16T11:30:00.000Z', true, REF)).toBe('seen 30m ago');
    expect(formatSeenAgo('2026-07-16T09:00:00.000Z', true, REF)).toBe('seen 3h ago');
  });

  it('falls through to online/offline for a stamp beyond skew tolerance, never a confident 0s', () => {
    expect(formatSeenAgo('2026-07-16T17:00:00.000Z', false, REF)).toBe('offline');
    expect(formatSeenAgo('2026-07-16T17:00:00.000Z', true, REF)).toBe('online');
  });
});

describe('formatUtcClock', () => {
  it('renders HH:MM:SS in UTC from an ISO instant', () => {
    expect(formatUtcClock('2026-07-16T11:00:00+00:00')).toBe('11:00:00');
  });

  it('returns empty for an absent or unparseable input', () => {
    expect(formatUtcClock(null)).toBe('');
    expect(formatUtcClock(undefined)).toBe('');
    expect(formatUtcClock('not-a-date')).toBe('');
  });
});

describe('formatClockTime', () => {
  it('renders zero-padded local HH:MM:SS from an epoch-ms instant', () => {
    expect(formatClockTime(new Date(2026, 6, 18, 9, 5, 3).getTime())).toBe('09:05:03');
  });
});
