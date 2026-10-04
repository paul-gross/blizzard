import type { runnerApi } from 'fleet';

import { conditionLabel, MISS_REASON_TEXT, sampledAgoLabel, subscriptionRows } from './app-subscriptions.model';

const NOW = Date.parse('2026-07-16T12:00:00.000Z');

describe('sampledAgoLabel', () => {
  it('renders the age of the newest attempt', () => {
    expect(sampledAgoLabel('2026-07-16T11:59:26.000Z', NOW)).toBe('-34s ago');
  });

  it('renders never with no attempt or a skew-broken timestamp', () => {
    expect(sampledAgoLabel(null, NOW)).toBe('never');
    expect(sampledAgoLabel('2026-07-16T13:00:00.000Z', NOW)).toBe('never');
  });
});

describe('conditionLabel', () => {
  it('reads never sampled before any attempt', () => {
    expect(conditionLabel(null, null)).toBe('never sampled');
  });

  it('reads ok on a successful attempt', () => {
    expect(conditionLabel(true, null)).toBe('ok');
  });

  it('names the operator text for every miss reason', () => {
    for (const reason of Object.keys(MISS_REASON_TEXT) as runnerApi.SampleMissReason[]) {
      expect(conditionLabel(false, reason)).toBe(`miss: ${MISS_REASON_TEXT[reason]}`);
    }
    expect(conditionLabel(false, 'credential_lapsed')).toBe('miss: credential lapsed: log in again');
  });

  it('reads unknown for a miss with no reason', () => {
    expect(conditionLabel(false, null)).toBe('miss: unknown');
  });
});

describe('subscriptionRows', () => {
  it('maps each subscription to its row, defaulting absent optionals to null', () => {
    const subs: runnerApi.SubscriptionView[] = [
      {
        slug: 'claude-max',
        name: 'Claude Max',
        provider: 'anthropic',
        ok: false,
        miss_reason: 'endpoint_unreachable',
        sampled_at: '2026-07-16T11:59:26.000Z',
        renewal: 'renews 2026-08-01',
      },
      { slug: 'chatgpt', name: 'ChatGPT', provider: 'openai' },
    ];
    expect(subscriptionRows(subs, NOW)).toEqual([
      {
        slug: 'claude-max',
        name: 'Claude Max',
        provider: 'anthropic',
        conditionLabel: 'miss: endpoint unreachable',
        sampledAgo: '-34s ago',
        renewalLabel: 'renews 2026-08-01',
        ok: false,
      },
      {
        slug: 'chatgpt',
        name: 'ChatGPT',
        provider: 'openai',
        conditionLabel: 'never sampled',
        sampledAgo: 'never',
        renewalLabel: null,
        ok: null,
      },
    ]);
  });
});
