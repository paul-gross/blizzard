import { SampleMissReason } from '../api/hub';
import { isLapsed, PACE_CELLS, paceLine, subscriptionLine } from './subscription-blocks.model';
import type { SubscriptionPace } from './subscription-pace.model';

/** A sampled, fresh subscription with one window — each case overrides what it pins. */
const pace = (fields: Partial<SubscriptionPace> = {}): SubscriptionPace => ({
  slug: 'claude-max',
  name: 'Claude Max',
  paceBars: [{ window: '5h', utilizationPct: 62, elapsedPct: 70 }],
  condition: null,
  sampledAt: '2026-07-16T11:55:00.000Z',
  refreshedLabel: 'refreshed 5m ago',
  refreshedTag: '[5m]',
  freshness: 'fresh',
  missReason: null,
  ...fields,
});

describe('paceLine', () => {
  it('fills a block per tenth of usage and notches the clock before its block', () => {
    const line = paceLine('5h', 62, 70);
    expect(line.cells).toHaveLength(PACE_CELLS);
    expect(line.cells.map((c) => c.on)).toEqual([true, true, true, true, true, true, false, false, false, false]);
    expect(line.cells.findIndex((c) => c.clockBefore)).toBe(7);
    expect(line.cells.some((c) => c.clockAfter)).toBe(false);
    expect(line.ahead).toBe(false);
    expect(line.hint).toBe('5h · 62% used · 70% of window');
  });

  it('reads usage past the clock as ahead', () => {
    expect(paceLine('7d', 80, 40).ahead).toBe(true);
  });

  it('notches a spent window after its last block, and clamps out-of-range figures', () => {
    const line = paceLine('5h', 140, 100);
    expect(line.cells.every((c) => c.on)).toBe(true);
    expect(line.cells.some((c) => c.clockBefore)).toBe(false);
    expect(line.cells[PACE_CELLS - 1].clockAfter).toBe(true);
    expect(paceLine('5h', -5, 0).cells.some((c) => c.on)).toBe(false);
  });
});

describe('isLapsed', () => {
  it('reads only a lapsed-credential condition as lapsed', () => {
    expect(isLapsed(pace({ condition: SampleMissReason.CREDENTIAL_LAPSED }))).toBe(true);
    expect(isLapsed(pace())).toBe(false);
  });
});

describe('subscriptionLine', () => {
  it('paces a sampled subscription window by window, carrying its age', () => {
    const line = subscriptionLine(pace());
    expect(line).toMatchObject({ slug: 'claude-max', name: 'Claude Max', state: 'paced', stale: false, age: '[5m]' });
    expect(line.windows.map((w) => w.window)).toEqual(['5h']);
  });

  it('reads a lapsed credential as lapsed, drawing no windows', () => {
    const line = subscriptionLine(pace({ condition: SampleMissReason.CREDENTIAL_LAPSED }));
    expect(line.state).toBe('lapsed');
    expect(line.windows).toEqual([]);
  });

  it('reads a never-sampled or windowless subscription as unsampled', () => {
    expect(subscriptionLine(pace({ sampledAt: null, refreshedTag: null })).state).toBe('unsampled');
    expect(subscriptionLine(pace({ paceBars: [] })).state).toBe('unsampled');
    expect(subscriptionLine(pace({ sampledAt: null, refreshedTag: undefined })).age).toBeNull();
  });

  it('dims a stale reading', () => {
    expect(subscriptionLine(pace({ freshness: 'stale' })).stale).toBe(true);
    expect(subscriptionLine(pace({ freshness: 'aging' })).stale).toBe(false);
  });
});
