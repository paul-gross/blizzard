import type { runnerApi } from 'fleet';

import {
  conditionLabel,
  MISS_REASON_TEXT,
  RENEWAL_FAILURE_TEXT,
  renewalLabel,
  sampledAgoLabel,
  subscriptionRows,
} from './app-subscriptions.model';

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

describe('renewalLabel', () => {
  const ATTEMPTED = '2026-07-16T11:59:26.000Z';

  it('reads null for a subscription never renewed', () => {
    expect(renewalLabel(null, null, null, NOW)).toBeNull();
  });

  it('reads renewed with its own attempt age', () => {
    expect(renewalLabel('renewed', null, ATTEMPTED, NOW)).toBe('renewed -34s ago');
  });

  it('names the operator text for every failure reason', () => {
    for (const reason of Object.keys(RENEWAL_FAILURE_TEXT) as runnerApi.RenewalFailureReason[]) {
      expect(renewalLabel('failed', reason, ATTEMPTED, NOW)).toBe(`failed (${RENEWAL_FAILURE_TEXT[reason]}) -34s ago`);
    }
    expect(renewalLabel('failed', 'timed_out', ATTEMPTED, NOW)).toBe('failed (timed out) -34s ago');
    expect(renewalLabel('failed', null, ATTEMPTED, NOW)).toBe('failed (unknown cause) -34s ago');
  });

  it('reads an attempt with no outcome on record as unrecorded', () => {
    expect(renewalLabel('unrecorded', null, ATTEMPTED, NOW)).toBe('attempted -34s ago, outcome not recorded');
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
        renewal_attempted_at: '2026-07-16T11:59:26.000Z',
        renewal_result: 'failed',
        renewal_failure_reason: 'vendor_refused',
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
        renewalLabel: 'failed (vendor refused) -34s ago',
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
