import { type FlagsResponse, type MeResponse, type MeTenant, Permission, type ProviderSummary, Role } from '../api/hub';

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
 * The operator identity `GET /api/me` resolves to without a login: an `admin`
 * member of the hub's one tenant. A spec that does assert gating stubs its own
 * narrower `MeResponse`/provider list instead.
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

/** A `GET /api/me` membership — an `admin` of tenant `ten_a`, `Acme` — with `overrides` applied. */
export function meTenant(overrides: Partial<MeTenant> = {}): MeTenant {
  return { tenant_id: 'ten_a', name: 'Acme', role: Role.ADMIN, permissions: [Permission.FLEET_VIEW], ...overrides };
}

/** A `GET /api/me` answer — a `contributor` holding no membership — with `overrides` applied. */
export function meResponse(overrides: Partial<MeResponse> = {}): MeResponse {
  return {
    user_id: 'usr_1',
    username: 'op',
    display_name: 'Op',
    role: Role.CONTRIBUTOR,
    permissions: [Permission.FLEET_VIEW],
    assignable_roles: [],
    tenants: [],
    hub_admin: false,
    ...overrides,
  };
}

/** One `GET /api/auth/providers` entry — GitHub — with `overrides` applied. */
export function providerSummary(overrides: Partial<ProviderSummary> = {}): ProviderSummary {
  return { name: 'github', display_name: 'GitHub', type: 'github', ...overrides };
}

/** A `GET /api/flags` answer — no flag set, none overridden — with `overrides` applied. */
export function flagsResponse(overrides: Partial<FlagsResponse> = {}): FlagsResponse {
  return { flags: {}, overridden: [], ...overrides };
}
