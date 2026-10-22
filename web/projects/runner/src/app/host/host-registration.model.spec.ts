import type { runnerApi } from 'fleet';

import { hostRegistrationSummary } from './host-registration.model';

describe('hostRegistrationSummary', () => {
  it('is null before the read lands', () => {
    expect(hostRegistrationSummary(undefined)).toBeNull();
  });

  it('counts one held runner in the singular and carries the last answer', () => {
    const summary = hostRegistrationSummary({
      state: 'registered',
      host_id: 'hs_1',
      held_runner_ids: ['rn_1'],
      answered_at: '2026-07-16T11:59:26.000Z',
      buffered_facts: 0,
      detail: null,
      host_name: null,
      tenant_id: null,
    });

    expect(summary?.tone).toBe('running');
    expect(summary?.lines).toEqual(['host hs_1', '1 runner held']);
    expect(summary?.answeredAt).toBe('2026-07-16T11:59:26.000Z');
  });

  it('reads version refused with the hub\'s sentence naming what to upgrade', () => {
    const detail = "runner 0.1.4 is older than this hub's oldest supported line, 0.2; upgrade the runner host to 0.2 or later, up to 0.3";
    const summary = hostRegistrationSummary({ state: 'version_refused', host_id: 'hs_1', detail, held_runner_ids: [], buffered_facts: 0, answered_at: null, host_name: null, tenant_id: null });

    expect(summary?.label).toBe('version refused');
    expect(summary?.tone).toBe('needs');
    expect(summary?.lines).toEqual(['host hs_1', detail]);
    expect(summary?.detail).toBe(detail);
  });

  it('counts one buffered fact in the singular', () => {
    expect(hostRegistrationSummary({
        state: 'no_hosts',
        buffered_facts: 1,
        answered_at: null,
        detail: null,
        held_runner_ids: [],
        host_id: null,
        host_name: null,
        tenant_id: null,
      })?.lines).toEqual(['1 fact buffered']);
  });

  it('reads a state this build does not know raw, in the idle tone, rather than throwing', () => {
    const state = 'suspended' as runnerApi.HostRegistrationView['state'];
    const summary = hostRegistrationSummary({
      state,
      buffered_facts: 0,
      answered_at: null,
      detail: 'held for review',
      held_runner_ids: [],
      host_id: 'hs_1',
      host_name: null,
      tenant_id: null,
    });

    expect(summary?.label).toBe('suspended');
    expect(summary?.tone).toBe('idle');
    expect(summary?.state).toBe('suspended');
    expect(summary?.lines).toEqual(['host hs_1', 'held for review']);
  });
});
