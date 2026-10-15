// exemplar: bzh:frontend-containers-compose, bzh:frontend-pending-override — the derivations the page composes.

import { PermitState, PermitVerb, type PermitView } from 'fleet';
import type { PermitMoveVars, PermitRevokeVars } from './permits.mutations';

/** The query param narrowing the listing to one holder. */
export const HOLDER_PARAM = 'holder';

/** The listing panel's label, naming the holder the rows are narrowed to when there is one. */
export function permitsLabel(holder: string | null): string {
  return holder === null ? 'Permits' : `Permits held by ${holder}`;
}

/** The label `permit`'s project shows by: its name, else its slug, else its id; `''` when the hub knows none. */
export function permitProjectLabel(permit: PermitView): string {
  return permit.project_name ?? permit.project_slug ?? permit.project_id ?? '';
}

/** One listed permit as the view renders it. */
export interface PermitRow {
  readonly permit: PermitView;
  /** The permit's state, or the state an in-flight verb will leave it in. */
  readonly state: PermitState;
  /** Whether a verb is in flight for this permit. */
  readonly pending: boolean;
}

const STATE_AFTER: Readonly<Record<PermitVerb, PermitState>> = {
  [PermitVerb.GRANT]: PermitState.GRANTED,
  [PermitVerb.SUSPEND]: PermitState.SUSPENDED,
  [PermitVerb.RESUME]: PermitState.GRANTED,
  [PermitVerb.REVOKE]: PermitState.REVOKED,
};

/** `permits` as rows, each in-flight move or revocation applied over its own permit. */
export function withPendingPermitChanges(
  permits: readonly PermitView[],
  moves: readonly PermitMoveVars[],
  revokes: readonly PermitRevokeVars[],
): PermitRow[] {
  const pending = new Map<string, PermitVerb>([
    ...moves.map((vars): [string, PermitVerb] => [vars.permitId, vars.verb]),
    ...revokes.map((vars): [string, PermitVerb] => [vars.permitId, PermitVerb.REVOKE]),
  ]);
  return permits.map((permit) => {
    const verb = pending.get(permit.permit_id);
    return verb === undefined
      ? { permit, state: permit.state, pending: false }
      : { permit, state: STATE_AFTER[verb], pending: true };
  });
}
