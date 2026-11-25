import type { UnknownHostValue } from 'fleet';

/** Whether a host record — a workspace or a subscription — is in force or retired; an unknown state is one this build
 * cannot name. */
export type HostRecordState = 'active' | 'retired' | UnknownHostValue;

/** What a host record carries beside its own facts: the revision an edit sends as `If-Match` (`null` on a host keeping
 * no records), its state, and every key of its table but its id, as stated. */
export interface HostRecordFacts {
  readonly revision: number | null;
  readonly state: HostRecordState;
  readonly settings: Readonly<Record<string, unknown>>;
}
