import type * as runnerApi from '../api/runner';
import { compactRef } from '../core/compact-ref';
import {
  conditionLabel,
  heldForLabel,
  MISS_REASON_TEXT,
  RENEWAL_FAILURE_TEXT,
  renewalLabel,
  runnerEnvironmentRows,
  runnerSubscriptionPaces,
  runnerWorkspaceRows,
  sampledAgoLabel,
} from './runner-host.model';

const NOW = Date.parse('2026-07-16T12:00:00.000Z');

describe('heldForLabel', () => {
  it('renders blank for an unheld environment', () => {
    expect(heldForLabel(null, NOW)).toBe('');
    expect(heldForLabel(undefined, NOW)).toBe('');
  });

  it('renders the held-for duration since the binding fact', () => {
    expect(heldForLabel('2026-07-16T11:18:00.000Z', NOW)).toBe('42m');
  });

  it('renders an em dash for a skew-broken timestamp', () => {
    expect(heldForLabel('2026-07-16T13:00:00.000Z', NOW)).toBe('—');
    expect(heldForLabel('not-a-timestamp', NOW)).toBe('—');
  });
});

/** Resolves the two runners these specs name to their display names. */
const RUNNER_NAMES: Readonly<Record<string, string>> = { rn_1: 'r-claude', rn_2: 'r-chatgpt' };
const runnerName = (runnerId: string): string => RUNNER_NAMES[runnerId] ?? runnerId;

describe('runnerEnvironmentRows', () => {
  it('maps a held and an unheld environment in wire order, naming the holder runner and project', () => {
    const envs: runnerApi.HostEnvironmentView[] = [
      {
        environment_id: 'e1',
        held: true,
        chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
        held_since: '2026-07-16T11:59:18.000Z',
        runner_id: 'rn_1',
        project: 'blizzard',
      },
      { environment_id: 'e2', held: false, chunk_id: null, held_since: null, runner_id: null, project: null },
    ];
    expect(runnerEnvironmentRows(envs, NOW, runnerName)).toEqual([
      {
        environmentId: 'e1',
        isHeld: true,
        chunkRef: compactRef('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9'),
        heldFor: '42s',
        holder: 'r-claude · blizzard',
      },
      { environmentId: 'e2', isHeld: false, chunkRef: '', heldFor: '', holder: null },
    ]);
  });

  it('treats an absent chunk_id and held flag as unheld', () => {
    expect(runnerEnvironmentRows([{ environment_id: 'e3' }], NOW, runnerName)).toEqual([
      { environmentId: 'e3', isHeld: false, chunkRef: '', heldFor: '', holder: null },
    ]);
  });

  it('names the runner alone when the holding chunk names no project', () => {
    const [row] = runnerEnvironmentRows([{ environment_id: 'e4', held: true, chunk_id: 'ch_1', runner_id: 'rn_2' }], NOW, runnerName);
    expect(row.holder).toBe('r-chatgpt');
  });
});

describe('runnerWorkspaceRows', () => {
  it('renders one row per declared workspace with its capacity and held count', () => {
    const rows = runnerWorkspaceRows(
      [
        { workspace_id: 'ws-a', max_environments: 3 },
        { workspace_id: 'ws-b', max_environments: 1 },
      ],
      [
        {
          workspace_id: 'ws-a',
          items: [
            { environment_id: 'e1', held: true, chunk_id: 'ch_1', held_since: '2026-07-16T11:59:18.000Z', runner_id: 'rn_1' },
            { environment_id: 'e2', held: false },
          ],
        },
      ],
      NOW,
      runnerName,
    );
    expect(rows.map((row) => [row.workspaceId, row.capacity, row.inUse])).toEqual([
      ['ws-a', 3, 1],
      ['ws-b', 1, 0],
    ]);
    expect(rows[0].environments.map((env) => env.environmentId)).toEqual(['e1', 'e2']);
    expect(rows[1].environments).toEqual([]);
  });

  it('still renders an environment group whose workspace is not declared, with no capacity', () => {
    const rows = runnerWorkspaceRows([], [{ workspace_id: 'ws-x', items: [{ environment_id: 'e9' }] }], NOW, runnerName);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ workspaceId: 'ws-x', capacity: null, inUse: 0 });
  });
});

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

describe('runnerSubscriptionPaces', () => {
  /** The hub's pace facts a runner row leaves empty — its sample line renders in their place. */
  const UNPACED = { paceBars: [], condition: null, sampledAt: null, refreshedLabel: null, freshness: null, missReason: null };

  it('maps each subscription to its row, defaulting absent optionals to null', () => {
    const subs: runnerApi.HostSubscriptionView[] = [
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
        spenders: ['rn_1', 'rn_2'],
      },
      { slug: 'chatgpt', name: 'ChatGPT', provider: 'openai' },
    ];
    expect(runnerSubscriptionPaces(subs, NOW, runnerName)).toEqual([
      {
        slug: 'claude-max',
        name: 'Claude Max',
        provider: 'anthropic',
        ...UNPACED,
        sample: { conditionLabel: 'miss: endpoint unreachable', sampledAgo: '-34s ago', ok: false },
        renewalLabel: 'failed (vendor refused) -34s ago',
        spenders: ['r-claude', 'r-chatgpt'],
      },
      {
        slug: 'chatgpt',
        name: 'ChatGPT',
        provider: 'openai',
        ...UNPACED,
        sample: { conditionLabel: 'never sampled', sampledAgo: 'never', ok: null },
        renewalLabel: null,
        spenders: [],
      },
    ]);
  });
});
