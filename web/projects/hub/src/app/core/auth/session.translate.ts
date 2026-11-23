import { type MeResponse, type MeTenant, openLookup } from 'fleet/shell';

import type { Membership, Permission, Session } from './session.domain';

type WirePermission = MeResponse['permissions'][number];

/** Each permission the build can name; one it cannot is dropped, since no check can ask for it. */
const permissionOf = openLookup<WirePermission, Permission | null>(
  {
    'fleet:view': 'fleet:view',
    'chunk:ingest': 'chunk:ingest',
    'chunk:control': 'chunk:control',
    'question:answer': 'question:answer',
    'gate:resolve': 'gate:resolve',
    'queue:reorder': 'queue:reorder',
    'runner:pause': 'runner:pause',
    'runner:retire': 'runner:retire',
    'runner:add': 'runner:add',
    'graph:edit': 'graph:edit',
    'user:manage': 'user:manage',
    'transcript:read': 'transcript:read',
    'analytics:admin': 'analytics:admin',
    'config:edit': 'config:edit',
    'tenant:admin': 'tenant:admin',
    'membership:grant-any': 'membership:grant-any',
    'hub-admin:grant': 'hub-admin:grant',
    'export:admin': 'export:admin',
    'settings:admin': 'settings:admin',
  },
  () => null,
);

function permissionsOf(wire: readonly string[]): readonly Permission[] {
  return wire.map(permissionOf).filter((permission): permission is Permission => permission !== null);
}

export function fromMeTenant(wire: MeTenant): Membership {
  return { tenant_id: wire.tenant_id, name: wire.name, permissions: permissionsOf(wire.permissions) };
}

export function fromMeResponse(wire: MeResponse): Session {
  return {
    user_id: wire.user_id,
    username: wire.username,
    display_name: wire.display_name,
    permissions: permissionsOf(wire.permissions),
    tenants: (wire.tenants ?? []).map(fromMeTenant),
    tenant_resolved: wire.role !== null,
  };
}
