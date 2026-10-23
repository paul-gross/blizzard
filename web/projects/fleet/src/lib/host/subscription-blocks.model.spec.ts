import { SampleMissReason } from '../api/hub';
import { isLapsed, subscriptionLine } from './subscription-blocks.model';
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
    expect(line.windows).toEqual([{ window: '5h', utilizationPct: 62, elapsedPct: 70 }]);
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
