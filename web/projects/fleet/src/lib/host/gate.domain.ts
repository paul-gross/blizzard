import type { UnknownHostValue } from '../core/host.domain';

/** Why a gate can never hold a chunk; `UnknownHostValue` carries a reason this build cannot name. */
export type KnownUnenforceableGateReason = 'hub-executed' | 'unknown-node';
export type UnenforceableGateReason = KnownUnenforceableGateReason | UnknownHostValue;

/** A gate a runner declares that no graph lets it enforce, with the reason. */
export interface UnenforceableGate {
  readonly node: string;
  readonly reason: UnenforceableGateReason;
}

/** The reason as rendered — a known reason as itself, an unknown one as the raw word. */
export function unenforceableReasonLabel(reason: UnenforceableGateReason): string {
  return typeof reason === 'string' ? reason : reason.raw;
}
