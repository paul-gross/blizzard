import { zoneOptions } from './zones';

const JAN = Date.UTC(2026, 0, 15, 12);
const JUL = Date.UTC(2026, 6, 15, 12);
const find = (now: number, zone: string) => zoneOptions(now).find((o) => o.value === zone);

describe('zoneOptions', () => {
  it('lists each zone once, sorted by name, with the name as its label', () => {
    const options = zoneOptions(JAN);
    const names = options.map((o) => o.value);
    expect(names).toEqual([...new Set(names)].sort());
    expect(options.every((o) => o.label === o.value)).toBe(true);
    expect(names).toContain('America/Chicago');
  });

  it('includes UTC exactly once, with a zero offset', () => {
    const utc = zoneOptions(JAN).filter((o) => o.value === 'UTC');
    expect(utc).toHaveLength(1);
    expect(utc[0].meta).toMatch(/^GMT$|^GMT[+-]0/);
  });

  it('shows the offset in force at the supplied time', () => {
    expect(find(JAN, 'America/Chicago')?.meta).toBe('GMT-6');
    expect(find(JUL, 'America/Chicago')?.meta).toBe('GMT-5');
    expect(find(JAN, 'Asia/Tokyo')?.meta).toBe('GMT+9');
  });
});
