import type { runnerApi } from 'fleet';

import { hubLink, identityRefusal, runnerLight } from './runner-health.model';

const VERDICT = (state: string, extra: Partial<runnerApi.IdentityVerdictView> = {}): runnerApi.IdentityVerdictView =>
  ({ state, store_runner_id: null, token_runner_id: null, checked_at: null, reset_command: 'reset it', reason: 'why', ...extra }) as runnerApi.IdentityVerdictView;

const RUNNER = (overrides: Partial<runnerApi.HostRunnerView> = {}): runnerApi.HostRunnerView => ({
  runner_id: 'rn_1',
  name: 'r1',
  state: 'serving',
  served: true,
  halted_reasons: [],
  pause: { local: false, hub: false, effective: false, local_reason: null },
  throttle: null,
  bindings: [],
  agents_in_use: 0,
  max_agents: 4,
  ...overrides,
});

const STATUS = (overrides: Record<string, unknown> = {}): runnerApi.RunnerStatusView =>
  ({ hub: { reachable: true }, identity: VERDICT('matched'), ...overrides }) as unknown as runnerApi.RunnerStatusView;

describe('identityRefusal', () => {
  it('is null while the identity checks out, goes unchecked, or is absent', () => {
    expect(identityRefusal(VERDICT('matched'))).toBeNull();
    expect(identityRefusal(VERDICT('unchecked'))).toBeNull();
    expect(identityRefusal(null)).toBeNull();
    expect(identityRefusal(undefined)).toBeNull();
  });

  it.each([
    ['mismatched', 'REFUSING'],
    ['refused', 'REFUSED'],
    ['not_held', 'NOT HELD'],
    ['tenant_differs', 'TENANT DIFFERS'],
    ['version_refused', 'VERSION REFUSED'],
  ])('badges %s as %s in needs', (state, label) => {
    expect(identityRefusal(VERDICT(state))).toMatchObject({ label, tone: 'needs' });
  });

  it('words the issue line for each refusal and carries the remedy', () => {
    expect(identityRefusal(VERDICT('mismatched', { token_runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3' }))).toMatchObject({
      issue: 'refusing — its token belongs to R-ABF3',
      title: 'reset it',
    });
    expect(identityRefusal(VERDICT('refused'))?.title).toBe('why — reset it');
    expect(identityRefusal(VERDICT('version_refused'))).toMatchObject({ issue: 'the hub refuses its version', title: 'why' });
  });

  it('reads a state it does not know raw, in idle', () => {
    expect(identityRefusal(VERDICT('quarantined'))).toMatchObject({ label: 'quarantined', issue: 'quarantined', tone: 'idle' });
  });
});

describe('runnerLight', () => {
  it('is red for a halted runner', () => {
    expect(runnerLight(RUNNER({ state: 'halted' }), undefined)).toBe('red');
  });

  it('is red once the hub is unreachable', () => {
    expect(runnerLight(RUNNER(), STATUS({ hub: { reachable: false } }))).toBe('red');
  });

  it('is red for a known identity refusal', () => {
    expect(runnerLight(RUNNER(), STATUS({ identity: VERDICT('version_refused') }))).toBe('red');
  });

  it('does not turn red for an identity state it does not know', () => {
    expect(runnerLight(RUNNER(), STATUS({ identity: VERDICT('quarantined') }))).toBe('green');
  });

  it('is amber while either brake holds the runner, off its status first and the host report otherwise', () => {
    const held = (local: boolean, hub: boolean) => ({ local, hub, effective: local || hub, local_reason: null });
    expect(runnerLight(RUNNER({ pause: held(true, false) }), undefined)).toBe('amber');
    expect(runnerLight(RUNNER({ pause: held(false, true) }), undefined)).toBe('amber');
    expect(runnerLight(RUNNER(), STATUS({ pause: held(true, false) }))).toBe('amber');
    expect(runnerLight(RUNNER({ pause: held(true, false) }), STATUS({ pause: held(false, false) }))).toBe('green');
  });

  it('is green when healthy, before or after its status lands', () => {
    expect(runnerLight(RUNNER(), undefined)).toBe('green');
    expect(runnerLight(RUNNER(), STATUS())).toBe('green');
  });
});

describe('hubLink', () => {
  it('reads all reachable done, none stale, some waiting', () => {
    expect(hubLink(2, 2)).toEqual({ label: 'CONNECTED', tone: 'done' });
    expect(hubLink(0, 2)).toEqual({ label: 'UNREACHABLE', tone: 'stale' });
    expect(hubLink(1, 2)).toEqual({ label: '1 / 2 CONNECTED', tone: 'waiting' });
  });

  it('reads a single runner by its one link', () => {
    expect(hubLink(1, 1).tone).toBe('done');
    expect(hubLink(0, 1).tone).toBe('stale');
  });
});
