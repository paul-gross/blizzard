import * as fs from 'node:fs';
import * as path from 'node:path';
import { devices, type PlaywrightTestConfig } from '@playwright/test';
import { TESTS_ROOT } from './env';
import type { ProfileFolder } from './profiles';

export const SERVICE_DIR = path.join(TESTS_ROOT, 'service');
export const SERVICE_RUNNER_DIR = path.join(SERVICE_DIR, 'runner');
export const SERVICE_FLEET_DIR = path.join(SERVICE_DIR, 'fleet');
export const UI_DIR = path.join(TESTS_ROOT, 'ui');

export type Project = NonNullable<PlaywrightTestConfig['projects']>[number];

/**
 * Where a project's tests ride a hub: `harness`, `harness-auth` and `harness-actions` ride the default, `auth` and
 * `actions` profiles, and `service-fleet[-x]` and `ui[-x]` ride `service-hub[-x]`. Any other project is its own
 * profile's.
 */
export function profileProject(projectName: string): string {
  if (projectName === 'harness') return 'service-hub';
  if (projectName === 'harness-auth') return 'service-hub-auth';
  if (projectName === 'harness-actions') return 'service-hub-actions';
  const fleet = /^service-fleet(-.+)?$/.exec(projectName);
  if (fleet !== null) return `service-hub${fleet[1] ?? ''}`;
  const ui = /^ui(-.+)?$/.exec(projectName);
  if (ui !== null) return `service-hub${ui[1] ?? ''}`;
  return projectName;
}

/**
 * The subfolders of a folder riding the hub profiles (the fleet folder, or the UI folder) named after a hub profile,
 * by the profile's name (`auth`, `a-b` for `a/b`), each as the project `<prefix>-<name>`.
 */
export function profileFolders(
  profiles: ProfileFolder[],
  root: string,
  prefix: string,
): ProfileFolder[] {
  const found: ProfileFolder[] = [];
  const walk = (dir: string): void => {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const child = path.join(dir, entry.name);
      const name = path.relative(root, child).split(path.sep).join('-');
      const profile = profiles.find((candidate) => candidate.name !== '' && candidate.name === name);
      if (profile !== undefined) found.push({ project: `${prefix}-${name}`, name, dir: child, file: profile.file });
      walk(child);
    }
  };
  walk(root);
  return found;
}

/** The subfolders of the fleet folder named after a hub profile. */
export function fleetProfileFolders(profiles: ProfileFolder[], root: string = SERVICE_FLEET_DIR): ProfileFolder[] {
  return profileFolders(profiles, root, 'service-fleet');
}

/** The subfolders of the UI folder named after a hub profile. */
export function uiProfileFolders(profiles: ProfileFolder[], root: string = UI_DIR): ProfileFolder[] {
  return profileFolders(profiles, root, 'ui');
}

/** A file named `profile.toml` below `root`, which only the hub folder may hold. */
export function strayProfiles(roots: string[] = [SERVICE_RUNNER_DIR, SERVICE_FLEET_DIR, UI_DIR]): string[] {
  const found: string[] = [];
  const walk = (dir: string): void => {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const child = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(child);
      else if (entry.name === 'profile.toml') found.push(child);
    }
  };
  roots.forEach(walk);
  return found;
}

/**
 * The service projects, a pure function of the folders found: a setup and teardown pair and a project per hub profile,
 * `service-runner` (no hub, no setup), `service-fleet` on the default hub profile's setup, and a `service-fleet-<name>`
 * on the setup of each hub profile a fleet subfolder is named after. Each spec runs under exactly one project.
 */
export function serviceProjects(
  profiles: ProfileFolder[],
  fleetFolders: ProfileFolder[],
  runnerDir: string = SERVICE_RUNNER_DIR,
  fleetDir: string = SERVICE_FLEET_DIR,
): Project[] {
  const profileFolders = profiles.filter((profile) => profile.name !== '');
  const setups = profiles.flatMap((profile): Project[] => [
    {
      name: `setup-${profile.project}`,
      testDir: 'harness/setup',
      testMatch: 'setup.spec.ts',
      teardown: `teardown-${profile.project}`,
    },
    { name: `teardown-${profile.project}`, testDir: 'harness/setup', testMatch: 'teardown.spec.ts' },
  ]);
  const hub = profiles.map((profile): Project => {
    const isDefault = profile.name === '';
    return {
      name: profile.project,
      testDir: profile.dir,
      // The default project ignores every profile subfolder, so a spec runs under exactly one project.
      testIgnore: isDefault ? profileFolders.map((folder) => path.join(folder.dir, '**')) : undefined,
      dependencies: [`setup-${profile.project}`],
      // A hub-wide setting only a few tests need costs one hub, run by one worker in sequence.
      workers: isDefault ? undefined : 1,
    };
  });
  const fleet = [{ project: 'service-fleet', name: '', dir: fleetDir }, ...fleetFolders].map((folder): Project => {
    const isDefault = folder.name === '';
    return {
      name: folder.project,
      testDir: folder.dir,
      testIgnore: isDefault ? fleetFolders.map((sub) => path.join(sub.dir, '**')) : undefined,
      dependencies: [`setup-${profileProject(folder.project)}`],
      workers: isDefault ? undefined : 1,
    };
  });
  // A runner-only test owns its mock hub and starts no real hub, so this project depends on no setup.
  const runner: Project = { name: 'service-runner', testDir: runnerDir };
  return [...setups, ...hub, runner, ...fleet];
}

/**
 * The browser projects: `ui` on the default hub profile's setup, and a `ui-<name>` on the setup of each hub profile a
 * UI subfolder is named after. Each drives Chromium against the board its test's hub serves; no other project has a
 * browser.
 */
export function uiProjects(uiFolders: ProfileFolder[], uiDir: string = UI_DIR): Project[] {
  return [{ project: 'ui', name: '', dir: uiDir }, ...uiFolders].map((folder): Project => {
    const isDefault = folder.name === '';
    return {
      name: folder.project,
      testDir: folder.dir,
      testIgnore: isDefault ? uiFolders.map((sub) => path.join(sub.dir, '**')) : undefined,
      dependencies: [`setup-${profileProject(folder.project)}`],
      workers: isDefault ? undefined : 1,
      use: { ...devices['Desktop Chrome'], browserName: 'chromium' },
    };
  });
}
