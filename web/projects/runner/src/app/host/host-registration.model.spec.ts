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
    });

    expect(summary?.tone).toBe('running');
    expect(summary?.lines).toEqual(['host hs_1', '1 runner held']);
    expect(summary?.answeredAt).toBe('2026-07-16T11:59:26.000Z');
  });

  it('counts one buffered fact in the singular', () => {
    expect(hostRegistrationSummary({ state: 'no_hosts', buffered_facts: 1 })?.lines).toEqual(['1 fact buffered']);
  });
});
