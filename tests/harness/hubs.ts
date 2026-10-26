import * as fs from 'node:fs';
import * as path from 'node:path';
import { runDir } from './env';
import { startForge } from './daemons/forge';
import { startHub } from './daemons/hub';
import { startIdp } from './daemons/idp';
import { type DaemonRecord, type LiveDaemon, recordOf, stopDaemon } from './daemons/process';
import { HubInternal } from './client/internal';
import type { TomlTable } from './profiles';

/** The fixture credential the common tenant's work source and repository name; the forge accepts any token. */
export const FIXTURE_SECRET = 'fixture-forge-token';
export const COMMON_OWNER = 'blizzard';
export const COMMON_REPOSITORY = 'common';
/** The work-source slug whose token prefix an ingest names, `<slug>:<number>`. */
export const COMMON_SOURCE = 'common';

/** One hub with the mock forge and stub IdP beside it, and the tenant a shared hub's tests act in. */
export interface HubCoordinates {
  index: number;
  url: string;
  forgeUrl: string;
  idpUrl: string;
  /** The common tenant's id; absent on a fresh hub, whose seeded tenant is its only one. */
  commonTenantId?: string;
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

/** A hub, a mock forge, and a stub IdP of its own, started together; `hubEnv` reaches the hub process only. */
export async function startStack(dir: string, index: number, profile: TomlTable, hubEnv: Record<string, string> = {}): Promise<HubStack> {
  const started = await Promise.allSettled([startHub(dir, profile, hubEnv), startForge(dir), startIdp(dir)]);
  const running = started.flatMap((outcome) => (outcome.status === 'fulfilled' ? [outcome.value] : []));
  const failure = started.find((outcome) => outcome.status === 'rejected');
  if (failure !== undefined) {
    // A start that failed stops the daemons beside it, so none is left running with nothing to stop it.
    await Promise.all(running.map(stopDaemon));
    throw (failure as PromiseRejectedResult).reason;
  }
  const [hub, forge, idp] = running as [LiveDaemon, DaemonRecord, DaemonRecord];
  return {
    hub,
    coordinates: {
      index,
      url: hub.url,
      forgeUrl: forge.url,
      idpUrl: idp.url,
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
 * repository, and its link to the `default` project. The work source and the repository each name a credential, so the
 * tenant holds one fixture secret; nothing furnished here is ever changed again.
 */
export async function furnishCommonTenant(hub: HubCoordinates): Promise<string> {
  const admin = new HubInternal(hub.url);
  const tenant = await admin.createTenant('common');
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
