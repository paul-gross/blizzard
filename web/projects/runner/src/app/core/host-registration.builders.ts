import type { HostRegistration } from './host-registration.domain';

/** The host's registration: registered, holding no runners. */
export function hostRegistration(overrides: Partial<HostRegistration> = {}): HostRegistration {
  return {
    state: 'registered',
    host_id: null,
    host_name: null,
    tenant_id: null,
    held_runner_ids: [],
    buffered_facts: 0,
    detail: null,
    answered_at: null,
    ...overrides,
  };
}
