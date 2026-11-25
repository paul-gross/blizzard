// exemplar: bzh:frontend-domain-boundary, bzh:frontend-open-vocabulary.

import { openLookup, PermitState as WireState, PermitVerb as WireVerb, type PermitView } from 'fleet';
import type { Permit, PermitState, PermitVerb } from './permits.domain';

const stateOf = openLookup<WireState, PermitState>({
  [WireState.REQUESTED]: 'requested',
  [WireState.GRANTED]: 'granted',
  [WireState.SUSPENDED]: 'suspended',
  [WireState.REVOKED]: 'revoked',
}, (raw) => ({ unlisted: raw }));

const verbOf = openLookup<WireVerb, PermitVerb | null>({
  [WireVerb.GRANT]: 'grant',
  [WireVerb.SUSPEND]: 'suspend',
  [WireVerb.RESUME]: 'resume',
  [WireVerb.REVOKE]: 'revoke',
}, () => null);

export function fromPermitView(wire: PermitView): Permit {
  return { ...wire, state: stateOf(wire.state), verbs: wire.verbs.map(verbOf).filter((verb): verb is PermitVerb => verb !== null) };
}
