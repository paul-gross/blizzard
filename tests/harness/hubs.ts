import * as fs from 'node:fs';
import * as path from 'node:path';
import { runDir } from './env';
import { startForge } from './daemons/forge';
import { startHub } from './daemons/hub';
import { startIdp } from './daemons/idp';
import { type DaemonRecord, type LiveDaemon, recordOf, stopDaemon } from './daemons/process';
import { HubInternal } from './client/internal';
import { expandProfile, type TomlTable } from './profiles';

/** The fixture credential the common tenant's work source and repository name; the forge accepts any token. */
export const FIXTURE_SECRET = 'fixture-forge-token';
export const COMMON_OWNER = 'blizzard';
export const COMMON_REPOSITORY = 'common';
/** The work-source slug whose token prefix an ingest names, `<slug>:<number>`. */
export const COMMON_SOURCE = 'common';

/**
 * What a hub's configuration says about authentication, when it is on: the superuser the hub bootstraps, the providers
 * it declares, and, once the superuser has signed in, the session and the user id every admin-acting client carries.
 */
export interface HubAuth {
  /** `auth.superuser`; absent when the profile names none. */
  superuser?: string;
  providers: string[];
  /** The superuser's `bz_session` value. Written by the setup project on a shared hub; a fresh hub signs in on demand. */
  session?: string;
  userId?: string;
}

/** The auth record of a profile as the hub reads it, or `undefined` when authentication is off (`auth.mode` unset or `none`). */
export function authOf(profile: TomlTable): HubAuth | undefined {
  const auth = profile.auth;
  if (typeof auth !== 'object' || auth === null || Array.isArray(auth)) return undefined;
  const { mode, superuser, oauth } = auth as { mode?: unknown; superuser?: unknown; oauth?: unknown };
  if (typeof mode !== 'string' || mode === 'none') return undefined;
  const declared = typeof oauth === 'object' && oauth !== null ? (oauth as { provider?: unknown }).provider : undefined;
  const providers = Array.isArray(declared) ? declared.flatMap((entry) => (typeof (entry as TomlTable).name === 'string' ? [(entry as TomlTable).name as string] : [])) : [];
  return { superuser: typeof superuser === 'string' ? superuser : undefined, providers };
}

/** One hub with the mock forge and stub IdP beside it, and the tenant a shared hub's tests act in. */
export interface HubCoordinates {
  index: number;
  url: string;
  forgeUrl: string;
  idpUrl: string;
  /** The common tenant's id; absent on a fresh hub, whose seeded tenant is its only one. */
  commonTenantId?: string;
  /** Present only when the hub's configuration turns authentication on; the one way to ask whether it is. */
  auth?: HubAuth;
  logPath: string;
  forgeLogPath: string;
  daemons: DaemonRecord[];
}

/** What `setup-<project>` writes to `.run/<run-id>/hubs/<project>.json` and the `hub` fixture reads. */
export interface ProjectHubs {
  project: string;
  hubs: HubCoordinates[];
}

export function hubsFile(project: string): string {
  return path.join(runDir(), 'hubs', `${project}.json`);
}

export function readProjectHubs(project: string): ProjectHubs {
  const file = hubsFile(project);
  if (!fs.existsSync(file)) {
    throw new Error(`${file} does not exist; the setup-${project} project writes it, so select ${project} with its setup.`);
  }
  return JSON.parse(fs.readFileSync(file, 'utf8')) as ProjectHubs;
}

/** A started stack: its coordinates, and the hub's live handle, which a fresh hub's lifecycle holds. */
export interface HubStack {
  coordinates: HubCoordinates;
  hub: LiveDaemon;
}

/**
 * A hub, a mock forge, and a stub IdP of its own. The forge and IdP start first, so the profile's `${forge}` and
 * `${idp}` expand to their URLs before the hub's file is written; `hubEnv` reaches the hub process only.
 */
export async function startStack(dir: string, index: number, profile: TomlTable, hubEnv: Record<string, string> = {}): Promise<HubStack> {
  const beside = await Promise.allSettled([startForge(dir), startIdp(dir)]);
  const running = beside.flatMap((outcome) => (outcome.status === 'fulfilled' ? [outcome.value] : []));
  const failure = beside.find((outcome) => outcome.status === 'rejected');
  if (failure !== undefined) {
    // A start that failed stops the daemons beside it, so none is left running with nothing to stop it.
    await Promise.all(running.map(stopDaemon));
    throw (failure as PromiseRejectedResult).reason;
  }
  const [forge, idp] = running as [DaemonRecord, DaemonRecord];
  let hub: LiveDaemon;
  const expanded = expandProfile(profile, { idp: idp.url, forge: forge.url });
  try {
    hub = await startHub(dir, expanded, hubEnv);
  } catch (error) {
    await Promise.all(running.map(stopDaemon));
    throw error;
  }
  return {
    hub,
    coordinates: {
      index,
      url: hub.url,
      forgeUrl: forge.url,
      idpUrl: idp.url,
      auth: authOf(expanded),
      logPath: hub.logPath,
      forgeLogPath: forge.logPath,
      // Plain records: the coordinates are serialized to the run's hubs file.
      daemons: [recordOf(hub), recordOf(forge), recordOf(idp)],
    },
  };
}

export async function startHubStack(dir: string, index: number, profile: TomlTable): Promise<HubCoordinates> {
  return (await startStack(dir, index, profile)).coordinates;
}

/**
 * Creates the common tenant and furnishes it with what a test needs to make work there and nothing more: the
 * repository `blizzard/common` on the hub's forge, and one apply declaring the forge as the tenant's work source, the
 * repository, and its link to the `default` project. `admin` carries the superuser's session on a hub with authentication
 * on, and `adminUserId` makes the superuser the tenant's admin. The work source and the repository each name a credential, so the
 * tenant holds one fixture secret; nothing furnished here is ever changed again.
 */
export async function furnishCommonTenant(hub: HubCoordinates, admin: HubInternal, adminUserId?: string): Promise<string> {
  const tenant = await admin.createTenant('common', adminUserId);
  await admin.call('POST', '/api/secrets', {
    tenant: tenant.tenant_id,
    body: { name: FIXTURE_SECRET, value: 'fixture-token' },
  });
  const created = await fetch(`${hub.forgeUrl}/_repos`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ owner: COMMON_OWNER, name: COMMON_REPOSITORY }),
  });
  if (created.status !== 201) throw new Error(`creating ${COMMON_OWNER}/${COMMON_REPOSITORY} answered ${created.status}: ${await created.text()}`);
  await admin.applyConfig(tenant.tenant_id, {
    version: 1,
    secrets: [FIXTURE_SECRET],
    work_sources: [
      {
        slug: COMMON_SOURCE,
        provider: 'github',
        locator: `${COMMON_OWNER}/${COMMON_REPOSITORY}`,
        api_base: hub.forgeUrl,
        secret: FIXTURE_SECRET,
      },
    ],
    repositories: [
      {
        slug: COMMON_REPOSITORY,
        forge_api_url: hub.forgeUrl,
        owner: COMMON_OWNER,
        repo: COMMON_REPOSITORY,
        base_branch: 'main',
        secret_name: FIXTURE_SECRET,
      },
    ],
    projects: [{ slug: 'default', name: 'Default', repositories: [COMMON_REPOSITORY], sources: [COMMON_SOURCE] }],
  });
  return tenant.tenant_id;
}
