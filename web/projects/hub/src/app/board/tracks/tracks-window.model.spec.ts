import { describe, expect, it } from 'vitest';

import {
  DEFAULT_TRACK_WINDOW,
  LOOKBACK_MS,
  TRACK_SPANS,
  liveCoverFor,
  rangeFor,
  resolveTrackWindow,
  spanChoice,
  windowForSpan,
  windowFromRange,
} from './tracks-window.model';

const MIN = 60_000;
const HOUR = 60 * MIN;
const ms = (iso: string): number => Date.parse(iso);

describe('resolveTrackWindow', () => {
  const now = ms('2026-07-01T10:00:45.500Z');

  it('floors a live windows end to the minute and sends no until', () => {
    const w = resolveTrackWindow(DEFAULT_TRACK_WINDOW, now);
    expect(w).toEqual({
      startMs: ms('2026-06-30T10:00:00Z'),
      endMs: ms('2026-07-01T10:00:00Z'),
      since: '2026-06-30T10:00:00.000Z',
      until: undefined,
      live: true,
    });
  });

  it('keeps a pinned windows exact end and sends it as until', () => {
    const end = ms('2026-06-30T08:30:15Z');
    const w = resolveTrackWindow({ spanMs: 6 * HOUR, endMs: end }, now);
    expect(w).toMatchObject({ startMs: end - 6 * HOUR, endMs: end, until: '2026-06-30T08:30:15.000Z', live: false });
    expect(w.since).toBe('2026-06-30T02:30:15.000Z');
  });
});

describe('windowFromRange', () => {
  const now = ms('2026-07-01T10:00:00Z');

  it('keeps following when the range ends within a minute of now', () => {
    expect(windowFromRange(now - 6 * HOUR, now - MIN, now)).toEqual({ spanMs: 6 * HOUR - MIN, endMs: null });
    expect(windowFromRange(now - 6 * HOUR, now + HOUR, now).endMs).toBeNull();
  });

  it('pins a range that ends earlier than that', () => {
    expect(windowFromRange(now - 8 * HOUR, now - 2 * HOUR, now)).toEqual({ spanMs: 6 * HOUR, endMs: now - 2 * HOUR });
  });

  it('never narrows below a minute', () => {
    expect(windowFromRange(now - 5000, now - 4000, now - 2 * HOUR).spanMs).toBe(MIN);
  });
});

describe('spanChoice and windowForSpan', () => {
  it('matches a following window to its preset chip', () => {
    for (const span of TRACK_SPANS) expect(spanChoice({ spanMs: span.ms, endMs: null })).toBe(span.value);
  });

  it('matches no chip once pinned or off the presets', () => {
    expect(spanChoice({ spanMs: 6 * HOUR, endMs: 1 })).toBeNull();
    expect(spanChoice({ spanMs: 5 * HOUR, endMs: null })).toBeNull();
  });

  it('selects a preset span, following now', () => {
    expect(windowForSpan('3d', { spanMs: HOUR, endMs: 5 })).toEqual({ spanMs: 72 * HOUR, endMs: null });
  });

  it('keeps the current window for an unknown value', () => {
    const current = { spanMs: HOUR, endMs: 5 };
    expect(windowForSpan('nope', current)).toBe(current);
  });
});

describe('rangeFor', () => {
  it('snaps outward to a grid a fraction of the span wide', () => {
    // A six hour span snaps to a three hour grid.
    const r = rangeFor(ms('2026-07-01T10:20:00Z'), ms('2026-07-01T16:20:00Z'));
    expect(r).toEqual({ since: '2026-07-01T09:00:00.000Z', until: '2026-07-01T18:00:00.000Z' });
  });

  it('reads one cell for any pan inside it', () => {
    const a = rangeFor(ms('2026-07-01T10:05:00Z'), ms('2026-07-01T16:05:00Z'));
    const b = rangeFor(ms('2026-07-01T10:40:00Z'), ms('2026-07-01T16:40:00Z'));
    expect(a.since).toBe(b.since);
  });

  it('uses the smallest cell, an hour, for a short span', () => {
    expect(rangeFor(ms('2026-07-01T10:20:00Z'), ms('2026-07-01T10:25:00Z'))).toEqual({
      since: '2026-07-01T10:00:00.000Z',
      until: '2026-07-01T11:00:00.000Z',
    });
  });

  it('keeps an exact cell boundary where it lies', () => {
    expect(rangeFor(ms('2026-07-01T09:00:00Z'), ms('2026-07-01T12:00:00Z'))).toEqual({
      since: '2026-07-01T09:00:00.000Z',
      until: '2026-07-01T12:00:00.000Z',
    });
  });

  it('clamps to the hubs widest read, keeping the end', () => {
    const r = rangeFor(ms('2026-06-01T00:00:00Z'), ms('2026-07-01T00:00:00Z'));
    expect(ms(r.until) - ms(r.since)).toBe(LOOKBACK_MS);
    expect(ms(r.until)).toBeGreaterThanOrEqual(ms('2026-07-01T00:00:00Z'));
  });
});

describe('liveCoverFor', () => {
  it('is the smallest preset-sized cover at least the span wide', () => {
    expect(liveCoverFor(HOUR)).toBe(6 * HOUR);
    expect(liveCoverFor(6 * HOUR)).toBe(6 * HOUR);
    expect(liveCoverFor(7 * HOUR)).toBe(24 * HOUR);
    expect(liveCoverFor(24 * HOUR + 1)).toBe(72 * HOUR);
    expect(liveCoverFor(168 * HOUR)).toBe(168 * HOUR);
    expect(liveCoverFor(169 * HOUR)).toBe(336 * HOUR);
  });

  it('never exceeds the hubs widest read', () => {
    expect(liveCoverFor(400 * HOUR)).toBe(LOOKBACK_MS);
  });
});
