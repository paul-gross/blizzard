// exemplar: bzh:frontend-domain-boundary — the permit values the presentation reads.

export type PermitMove = 'grant' | 'suspend' | 'resume';
export type PermitVerb = PermitMove | 'revoke';
export type PermitState = 'requested' | 'granted' | 'suspended' | 'revoked' | { readonly unlisted: string };

export interface Permit {
  readonly permit_id: string;
  readonly project_id: string | null;
  readonly project_slug: string | null;
  readonly project_name: string | null;
  readonly holder: string;
  readonly capability: string;
  readonly state: PermitState;
  readonly verbs: readonly PermitVerb[];
  readonly requested_at: string;
  readonly requested_by: string;
  readonly changed_at: string | null;
  readonly changed_by: string | null;
  readonly revocation_note: string | null;
}

export function permitStateLabel(state: PermitState): string {
  return typeof state === 'string' ? state : state.unlisted;
}
