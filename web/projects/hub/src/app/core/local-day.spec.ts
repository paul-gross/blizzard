import { startOfLocalDayIso, startOfPreviousLocalDayIso } from './local-day';

describe('startOfPreviousLocalDayIso', () => {
  it("is exactly one calendar day before today's own local midnight", () => {
    const now = new Date(2026, 6, 16, 15, 30).getTime();

    expect(startOfPreviousLocalDayIso(now)).toBe(new Date(2026, 6, 15, 0, 0, 0).toISOString());
    expect(startOfLocalDayIso(now)).toBe(new Date(2026, 6, 16, 0, 0, 0).toISOString());
  });

  it('rolls a month/year boundary over correctly, deriving from the same construction as today', () => {
    const now = new Date(2026, 0, 1, 2, 0).getTime();

    expect(startOfPreviousLocalDayIso(now)).toBe(new Date(2025, 11, 31, 0, 0, 0).toISOString());
  });
});
