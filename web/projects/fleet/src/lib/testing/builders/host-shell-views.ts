import type {
  HarnessHealthView,
  HostEnvironmentView,
  HostRegistrationView,
  HostSubscriptionView,
  SelfTestView,
  ServedProjectStatusView,
} from '../../api/runner';

/** One environment in a host workspace — free unless the caller holds it. */
export function hostEnvironmentView(
  overrides: Pick<HostEnvironmentView, 'environment_id'> & Partial<HostEnvironmentView>,
): HostEnvironmentView {
  return { chunk_id: null, held: false, held_since: null, project: null, runner_id: null, ...overrides };
}

/** The host's hub registration: registered, holding no runners. */
export function hostRegistrationView(overrides: Partial<HostRegistrationView> = {}): HostRegistrationView {
  return {
    answered_at: null,
    buffered_facts: 0,
    detail: null,
    held_runner_ids: [],
    host_id: null,
    host_name: null,
    state: 'registered',
    tenant_id: null,
    ...overrides,
  };
}

/** One project a runner serves, resolved and answered. */
export function servedProjectStatusView(
  overrides: Pick<ServedProjectStatusView, 'entry'> & Partial<ServedProjectStatusView>,
): ServedProjectStatusView {
  return { answered: true, color: null, name: null, project_id: null, resolved: true, slug: null, ...overrides };
}

/** A selftest run, still running with no checks yet. */
export function selfTestView(overrides: Partial<SelfTestView> = {}): SelfTestView {
  return { checks: [], error: null, harness: 'mock', id: 'st_1', status: 'running', ...overrides };
}

/** One host subscription, sampled healthy with no spenders and no usage windows. */
export function hostSubscriptionView(
  overrides: Pick<HostSubscriptionView, 'slug' | 'name' | 'provider'> & Partial<HostSubscriptionView>,
): HostSubscriptionView {
  return {
    miss_reason: null,
    ok: true,
    renewal_attempted_at: null,
    renewal_failure_reason: null,
    renewal_result: null,
    sampled_at: null,
    spenders: [],
    windows: [],
    windows_sampled_at: null,
    ...overrides,
  };
}

/** One harness's health row: available, undegraded, at no known version. */
export function harnessHealthView(
  overrides: Pick<HarnessHealthView, 'harness_id'> & Partial<HarnessHealthView>,
): HarnessHealthView {
  return { admitted_range: null, available: true, cause: null, degradations: [], version: null, ...overrides };
}
