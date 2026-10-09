import { type MeResponse, Permission, Role } from '../api/hub';

/** The operator's permissions in its tenant. */
const OPERATOR_PERMISSIONS: MeResponse['permissions'] = [
  Permission.FLEET_VIEW,
  Permission.CHUNK_INGEST,
  Permission.CHUNK_CONTROL,
  Permission.QUESTION_ANSWER,
  Permission.GATE_RESOLVE,
  Permission.QUEUE_REORDER,
  Permission.RUNNER_PAUSE,
  Permission.GRAPH_EDIT,
  Permission.USER_MANAGE,
  Permission.TRANSCRIPT_READ,
];

/**
 * The operator identity `GET /api/me` resolves to under `auth.mode = "none"`
 * (the default-preserving fallback, which needs no login): an `admin` member of the
 * hub's one tenant. Every spec that mounts
 * the app root (or otherwise depends on its session gate settling to `'ready'`)
 * stubs `/api/me` with this, so chrome/route assertions written before auth existed
 * keep exercising the "everything visible, no login" behavior without asserting
 * anything about auth itself — a spec that *does* want to assert gating stubs its
 * own narrower `MeResponse`/provider list instead.
 */
export const OPERATOR_ME_RESPONSE: MeResponse = {
  user_id: 'operator',
  username: 'operator',
  display_name: 'operator',
  role: Role.ADMIN,
  permissions: OPERATOR_PERMISSIONS,
  assignable_roles: Object.values(Role).filter((role) => role !== Role.SUPERUSER),
  tenants: [{ tenant_id: 'ten_operator', name: 'operator', role: Role.ADMIN, permissions: OPERATOR_PERMISSIONS }],
  hub_admin: true,
};
