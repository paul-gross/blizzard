import type { RunnerSubscription } from '../core/host.domain';
import {
  conditionLabel,
  MISS_REASON_TEXT,
  RENEWAL_FAILURE_TEXT,
  renewalLabel,
  runnerSubscriptionLines,
  runnerSubscriptionPaces,
  sampledAgoLabel,
} from './runner-host.model';

const NOW = Date.parse('2026-07-16T12:00:00.000Z');

/** The subscription facts a never-attempted subscription reports as empty. */
const UNREPORTED = {
  miss_reason: null,
  ok: null,
  renewal_attempted_at: null,
  renewal_failure_reason: null,
  renewal_result: null,
  sampled_at: null,
  spenders: [],
  windows: [],
  windows_sampled_at: null,
  condition: null,
} satisfies Omit<RunnerSubscription, 'slug' | 'name' | 'provider'>;

/** Resolves the two runners these specs name to their display names. */
const RUNNER_NAMES: Readonly<Record<string, string>> = { rn_1: 'r-claude', rn_2: 'r-chatgpt' };
const runnerName = (runnerId: string): string => RUNNER_NAMES[runnerId] ?? runnerId;

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
    for (const reason of MISS_REASON_TEXT.keys) {
      expect(conditionLabel(false, reason)).toBe(`miss: ${MISS_REASON_TEXT(reason)}`);
    }
    expect(conditionLabel(false, 'credential_lapsed')).toBe('miss: credential lapsed: log in again');
  });

  it('reads unknown for a miss with no reason', () => {
    expect(conditionLabel(false, null)).toBe('miss: unknown');
  });

  it('reads a miss reason this build does not know raw', () => {
    expect(conditionLabel(false, { kind: 'unknown', raw: 'quota_exceeded' })).toBe('miss: quota_exceeded');
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
    for (const reason of RENEWAL_FAILURE_TEXT.keys) {
      expect(renewalLabel('failed', reason, ATTEMPTED, NOW)).toBe(`failed (${RENEWAL_FAILURE_TEXT(reason)}) -34s ago`);
    }
    expect(renewalLabel('failed', 'timed_out', ATTEMPTED, NOW)).toBe('failed (timed out) -34s ago');
    expect(renewalLabel('failed', null, ATTEMPTED, NOW)).toBe('failed (unknown cause) -34s ago');
  });

  it('reads an attempt with no outcome on record as unrecorded', () => {
    expect(renewalLabel('unrecorded', null, ATTEMPTED, NOW)).toBe('attempted -34s ago, outcome not recorded');
  });

  it('reads a result or failure reason this build does not know raw', () => {
    expect(renewalLabel({ kind: 'unknown', raw: 'deferred' }, null, ATTEMPTED, NOW)).toBe('deferred -34s ago');
    expect(renewalLabel('failed', { kind: 'unknown', raw: 'revoked' }, ATTEMPTED, NOW)).toBe('failed (revoked) -34s ago');
  });
});

describe('runnerSubscriptionPaces', () => {
  /** The pace facts a row with no sampled windows leaves empty. */
  const UNPACED = { paceBars: [], condition: null, sampledAt: null, refreshedLabel: null, refreshedTag: null, freshness: null };

  it('carries a missed attempt with no windows as its sample line, a never-attempted one with no sample', () => {
    const subs: RunnerSubscription[] = [
      {
        ...UNREPORTED,
        slug: 'claude-max',
        name: 'Claude Max',
        provider: 'anthropic',
        ok: false,
        miss_reason: 'endpoint_unreachable',
        sampled_at: '2026-07-16T11:59:26.000Z',
        renewal_attempted_at: '2026-07-16T11:59:26.000Z',
        renewal_result: 'failed',
        renewal_failure_reason: 'vendor_refused',
        spenders: ['rn_1', 'rn_2'],
      },
      { ...UNREPORTED, slug: 'chatgpt', name: 'ChatGPT', provider: 'openai' },
    ];
    expect(runnerSubscriptionPaces(subs, NOW, runnerName)).toEqual([
      {
        slug: 'claude-max',
        name: 'Claude Max',
        provider: 'anthropic',
        ...UNPACED,
        missReason: 'endpoint_unreachable',
        sample: { conditionLabel: 'miss: endpoint unreachable', sampledAgo: '-34s ago', ok: false },
        renewalLabel: 'failed (vendor refused) -34s ago',
        spenders: ['r-claude', 'r-chatgpt'],
      },
      {
        slug: 'chatgpt',
        name: 'ChatGPT',
        provider: 'openai',
        ...UNPACED,
        missReason: null,
        sample: null,
        renewalLabel: null,
        spenders: [],
      },
    ]);
  });

  it('paces the newest sampled snapshot\'s windows, aged against that snapshot, with no sample line', () => {
    const [pace] = runnerSubscriptionPaces(
      [
        {
          ...UNREPORTED,
          slug: 'claude-max',
          name: 'Claude Max', provider: 'anthropic',
          ok: true,
          sampled_at: '2026-07-16T11:59:00.000Z',
          windows_sampled_at: '2026-07-16T11:55:00.000Z',
          windows: [{ window: '5h', utilization_pct: 62, resets_at: '2026-07-16T13:30:00.000Z', window_seconds: 5 * 3600 }],
        },
      ],
      NOW,
      runnerName,
    );
    expect(pace.paceBars).toEqual([{ window: '5h', utilizationPct: 62, elapsedPct: 70 }]);
    expect(pace).toMatchObject({ sampledAt: '2026-07-16T11:55:00.000Z', freshness: 'fresh', condition: null, missReason: null, sample: null });
    expect(pace.refreshedLabel).not.toBeNull();
    expect(pace.refreshedTag).not.toBeNull();
  });

  it('reads a lapsed credential as lapsed, whatever windows it last carried', () => {
    const [pace] = runnerSubscriptionPaces(
      [{ ...UNREPORTED, slug: 'claude-max', name: 'Claude Max', provider: 'anthropic', ok: false, miss_reason: 'credential_lapsed', condition: 'credential_lapsed', sampled_at: '2026-07-16T11:59:00.000Z' }],
      NOW,
      runnerName,
    );
    expect(pace).toMatchObject({ condition: 'credential_lapsed', missReason: 'credential_lapsed', sample: null });
  });

  it('reads the wire condition, not the raw miss, and preserves an unknown condition', () => {
    const [raw, unknown] = runnerSubscriptionPaces(
      [
        { ...UNREPORTED, slug: 'a', name: 'A', provider: 'anthropic', ok: false, miss_reason: 'credential_lapsed' },
        { ...UNREPORTED, slug: 'b', name: 'B', provider: 'anthropic', ok: false, miss_reason: 'credential_lapsed', condition: { kind: 'unknown', raw: 'mystery' } },
      ],
      NOW,
      runnerName,
    );
    expect(raw.condition).toBeNull();
    expect(unknown.condition).toEqual({ kind: 'unknown', raw: 'mystery' });
  });
});

describe('runnerSubscriptionLines', () => {
  it('draws sampled windows as a paced line, a lapsed credential as lapsed, and a miss with no windows as its note', () => {
    const lines = runnerSubscriptionLines(
      [
        {
          ...UNREPORTED,
          slug: 'paced',
          name: 'Paced', provider: 'anthropic',
          ok: true,
          windows_sampled_at: '2026-07-16T11:55:00.000Z',
          windows: [{ window: '5h', utilization_pct: 62, resets_at: '2026-07-16T13:30:00.000Z', window_seconds: 5 * 3600 }],
        },
        { ...UNREPORTED, slug: 'lapsed', name: 'Lapsed', provider: 'anthropic', ok: false, miss_reason: 'credential_lapsed', condition: 'credential_lapsed' },
        { ...UNREPORTED, slug: 'missed', name: 'Missed', provider: 'anthropic', ok: false, miss_reason: 'endpoint_unreachable', sampled_at: '2026-07-16T11:59:26.000Z' },
        { ...UNREPORTED, slug: 'never', name: 'Never', provider: 'anthropic' },
      ],
      NOW,
    );
    expect(lines.map((line) => [line.slug, line.state, line.note])).toEqual([
      ['paced', 'paced', null],
      ['lapsed', 'lapsed', null],
      ['missed', 'unsampled', 'miss: endpoint unreachable'],
      ['never', 'unsampled', null],
    ]);
    expect(lines[0].windows).toEqual([{ window: '5h', utilizationPct: 62, elapsedPct: 70 }]);
  });
});
