import { vi } from 'vitest';

import { startOfLocalDayIso, startOfPreviousLocalDayIso } from './local-day';

describe('startOfPreviousLocalDayIso', () => {
  afterEach(() => vi.useRealTimers());

  it("is exactly one calendar day before today's own local midnight", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 16, 15, 30));

    expect(startOfPreviousLocalDayIso()).toBe(new Date(2026, 6, 15, 0, 0, 0).toISOString());
    expect(startOfLocalDayIso()).toBe(new Date(2026, 6, 16, 0, 0, 0).toISOString());
  });

  it('rolls a month/year boundary over correctly, deriving from the same construction as today', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 0, 1, 2, 0));

    expect(startOfPreviousLocalDayIso()).toBe(new Date(2025, 11, 31, 0, 0, 0).toISOString());
  });
});
