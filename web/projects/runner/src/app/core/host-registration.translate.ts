import type { UnknownHostValue } from 'fleet';
import type { runnerApi } from 'fleet/runner-shell';
import { openLookup } from 'fleet/shell';

import type { HostRegistration, HostRegistrationState } from './host-registration.domain';

const states = openLookup<runnerApi.HostRegistrationState, HostRegistrationState>(
  {
    unregistered: 'unregistered',
    pending: 'pending',
    registered: 'registered',
    refused: 'refused',
    no_hosts: 'no_hosts',
    unreachable: 'unreachable',
    version_refused: 'version_refused',
  },
  (raw): UnknownHostValue => ({ kind: 'unknown', raw }),
);

export function fromHostRegistrationView(wire: runnerApi.HostRegistrationView): HostRegistration {
  return {
    state: states(wire.state),
    host_id: wire.host_id ?? null,
    host_name: wire.host_name ?? null,
    tenant_id: wire.tenant_id ?? null,
    held_runner_ids: [...(wire.held_runner_ids ?? [])],
    buffered_facts: wire.buffered_facts ?? 0,
    detail: wire.detail ?? null,
    answered_at: wire.answered_at ?? null,
  };
}
