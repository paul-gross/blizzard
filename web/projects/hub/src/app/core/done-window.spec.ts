import { describe, expect, it } from 'vitest';

import { BOARD_DONE_WINDOW_HOURS, withinDoneWindow } from './done-window';

describe('withinDoneWindow', () => {
  const now = Date.parse('2026-11-02T12:00:00Z');
  const hoursAgo = (hours: number) =>
    new Date(now - hours * 60 * 60 * 1000).toISOString();

  it("holds an instant up to the window's edge, and none past it or unknown", () => {
    expect(withinDoneWindow(hoursAgo(1), now)).toBe(true);
    expect(withinDoneWindow(hoursAgo(BOARD_DONE_WINDOW_HOURS), now)).toBe(true);
    expect(withinDoneWindow(hoursAgo(BOARD_DONE_WINDOW_HOURS + 1), now)).toBe(
      false,
    );
    expect(withinDoneWindow(null, now)).toBe(false);
  });
});
