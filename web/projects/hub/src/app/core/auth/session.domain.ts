/** One permission a session can carry — the permissions this build can name. */
export type Permission =
  | 'fleet:view'
  | 'chunk:ingest'
  | 'chunk:control'
  | 'question:answer'
  | 'gate:resolve'
  | 'queue:reorder'
  | 'runner:pause'
  | 'runner:retire'
  | 'runner:add'
  | 'graph:edit'
  | 'user:manage'
  | 'transcript:read'
  | 'analytics:admin'
  | 'config:edit'
  | 'tenant:admin'
  | 'membership:grant-any'
  | 'hub-admin:grant'
  | 'export:admin'
  | 'settings:admin';

/** One tenant the signed-in person holds a membership in, with that membership's permissions. */
export interface Membership {
  readonly tenant_id: string;
  readonly name: string;
  readonly permissions: readonly Permission[];
}

/** The signed-in person as the board reads them. `permissions` are the ones held in the tenant the hub resolved for
 * the read; `tenant_resolved` is false when the read named no tenant the hub could settle, so the permissions name
 * none yet. */
export interface Session {
  readonly user_id: string;
  readonly username: string;
  readonly display_name: string;
  readonly permissions: readonly Permission[];
  readonly tenants: readonly Membership[];
  readonly tenant_resolved: boolean;
}

/** Which tenant the board acts in, settled from the caller's memberships:
 * `none` — no membership, so nothing to enter; `choose` — several, and neither this tab's tenant nor the browser's
 * last choice is still one of them; `selected` — the membership the board acts in. */
export type TenantResolution =
  | { readonly kind: 'none' }
  | { readonly kind: 'choose'; readonly tenants: readonly Membership[] }
  | {
      readonly kind: 'selected';
      readonly tenant: Membership;
      readonly tenants: readonly Membership[];
    };

/** Settles the tenant from the caller's memberships: the only one when there is one; with several, `held` (the tab's
 * tenant) while it is still a membership, else `last` (the browser's last choice) while it is, else a choice. */
export function resolveTenant(
  tenants: readonly Membership[],
  held: string | null,
  last: string | null,
): TenantResolution {
  if (tenants.length === 0) return { kind: 'none' };
  if (tenants.length === 1)
    return { kind: 'selected', tenant: tenants[0], tenants };
  const kept =
    tenants.find((t) => t.tenant_id === held) ??
    tenants.find((t) => t.tenant_id === last);
  return kept === undefined
    ? { kind: 'choose', tenants }
    : { kind: 'selected', tenant: kept, tenants };
}

/** The session as the board reads it, in the selected tenant. The hub answers `permissions` for the tenant the request
 * named, and every call names the selected one, so a resolved answer is already that membership's and stands. A read
 * made before the tenant was settled — no header among several memberships, or one naming a tenant the caller has
 * left — resolves none; the selected membership's permissions stand in for it. */
export function inSelectedTenant(
  session: Session,
  resolution: TenantResolution,
): Session {
  if (resolution.kind !== 'selected' || session.tenant_resolved) return session;
  return {
    ...session,
    permissions: resolution.tenant.permissions,
    tenant_resolved: true,
  };
}

/** Whether a session carries `permission` — the single selector every capability-gated control reads
 * (`hasPermission(me(), 'user:manage')`) rather than re-deriving its own role check. `null`/`undefined`
 * (unauthenticated, or not yet resolved) never carries a permission. */
export function hasPermission(
  session: Session | null | undefined,
  permission: Permission,
): boolean {
  return session !== null && session !== undefined && session.permissions.includes(permission);
}
