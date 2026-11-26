import type { UnknownHostValue } from 'fleet/core';

export type KnownHostRegistrationState =
  | 'unregistered'
  | 'pending'
  | 'registered'
  | 'refused'
  | 'no_hosts'
  | 'unreachable'
  | 'version_refused';

/** The host's registration state at its hub; a state this build does not know keeps its spelling. */
export type HostRegistrationState = KnownHostRegistrationState | UnknownHostValue;

/** The host's registration at its hub, as its latest report found it. */
export interface HostRegistration {
  readonly state: HostRegistrationState;
  readonly host_id: string | null;
  readonly host_name: string | null;
  readonly tenant_id: string | null;
  readonly held_runner_ids: readonly string[];
  readonly buffered_facts: number;
  /** The hub's own sentence on the state, `null` when it gave none. */
  readonly detail: string | null;
  /** When the hub last answered the host's report, `null` before any answer. */
  readonly answered_at: string | null;
}
