import * as fs from 'node:fs';
import * as path from 'node:path';
import { expect, test } from '@playwright/test';
import { assertProfilesSound, discoverProfiles } from '../profiles';
import { fleetProfileFolders, profileProject, serviceProjects, strayProfiles } from '../projects';

/** A service tree: the hub folder with the given profile subfolders, plus whatever `extra` directories name. */
function tree(profiles: string[], extra: string[] = []): string {
  const root = fs.mkdtempSync(path.join(process.env.BLIZZARD_TMPDIR ?? '/tmp', 'projects-'));
  fs.mkdirSync(path.join(root, 'hub'), { recursive: true });
  fs.writeFileSync(path.join(root, 'hub', 'profile.toml'), '');
  for (const name of profiles) {
    fs.mkdirSync(path.join(root, 'hub', ...name.split('/')), { recursive: true });
    fs.writeFileSync(path.join(root, 'hub', ...name.split('/'), 'profile.toml'), '');
  }
  for (const dir of extra) fs.mkdirSync(path.join(root, ...dir.split('/')), { recursive: true });
  return root;
}

function table(root: string) {
  const profiles = discoverProfiles(path.join(root, 'hub'));
  const projects = serviceProjects(profiles, fleetProfileFolders(profiles, path.join(root, 'fleet')), path.join(root, 'runner'), path.join(root, 'fleet'));
  return (name: string) => projects.find((project) => project.name === name);
}

test('service-runner depends on no setup and takes no worker cap', () => {
  const root = tree(['auth']);
  const runner = table(root)('service-runner');
  expect(runner).toEqual({ name: 'service-runner', testDir: path.join(root, 'runner') });
  fs.rmSync(root, { recursive: true });
});

test('service-fleet rides the default hub setup and ignores every profile subfolder', () => {
  const root = tree(['auth', 'route-token-enforce'], ['fleet/auth', 'fleet/route-token-enforce', 'fleet/plain']);
  const fleet = table(root)('service-fleet');
  expect(fleet?.dependencies).toEqual(['setup-service-hub']);
  expect(fleet?.workers).toBeUndefined();
  expect(fleet?.testDir).toBe(path.join(root, 'fleet'));
  expect(fleet?.testIgnore).toEqual([path.join(root, 'fleet', 'auth', '**'), path.join(root, 'fleet', 'route-token-enforce', '**')]);
  fs.rmSync(root, { recursive: true });
});

test('a fleet subfolder named after a hub profile rides that profile’s setup with one worker', () => {
  const root = tree(['auth', 'route-token-enforce'], ['fleet/auth', 'fleet/route-token-enforce']);
  const find = table(root);
  expect(find('service-fleet-auth')).toMatchObject({ testDir: path.join(root, 'fleet', 'auth'), dependencies: ['setup-service-hub-auth'], workers: 1 });
  expect(find('service-fleet-route-token-enforce')).toMatchObject({ dependencies: ['setup-service-hub-route-token-enforce'], workers: 1 });
  fs.rmSync(root, { recursive: true });
});

test('a fleet subfolder named actions rides the actions profile’s setup with one worker', () => {
  const root = tree(['actions'], ['fleet/actions']);
  expect(table(root)('service-fleet-actions')).toMatchObject({ testDir: path.join(root, 'fleet', 'actions'), dependencies: ['setup-service-hub-actions'], workers: 1 });
  fs.rmSync(root, { recursive: true });
});

test('a fleet subfolder naming no hub profile is no project', () => {
  const root = tree(['auth'], ['fleet/plain']);
  expect(table(root)('service-fleet-plain')).toBeUndefined();
  fs.rmSync(root, { recursive: true });
});

test('the hub projects and their setup and teardown pairs are unchanged', () => {
  const root = tree(['auth']);
  const find = table(root);
  expect(find('service-hub')).toMatchObject({ dependencies: ['setup-service-hub'], testIgnore: [path.join(root, 'hub', 'auth', '**')] });
  expect(find('service-hub-auth')).toMatchObject({ dependencies: ['setup-service-hub-auth'], workers: 1 });
  expect(find('setup-service-hub-auth')).toMatchObject({ teardown: 'teardown-service-hub-auth' });
  fs.rmSync(root, { recursive: true });
});

test('a profile.toml under the runner or fleet folder is refused, naming its file', () => {
  const root = tree([], ['runner/store', 'fleet/auth']);
  fs.writeFileSync(path.join(root, 'fleet', 'auth', 'profile.toml'), '');
  fs.writeFileSync(path.join(root, 'runner', 'store', 'profile.toml'), '');
  const strays = strayProfiles([path.join(root, 'runner'), path.join(root, 'fleet')]);
  expect(strays).toHaveLength(2);
  expect(() => assertProfilesSound([], strays)).toThrow(/fleet\/auth\/profile\.toml[\s\S]*hub profile lives in service\/hub/);
  fs.rmSync(root, { recursive: true });
});

test('the fleet projects map to the hub projects, for the shared hub and the fresh-hub profile alike', () => {
  expect(profileProject('service-fleet')).toBe('service-hub');
  expect(profileProject('service-fleet-auth')).toBe('service-hub-auth');
  expect(profileProject('service-fleet-route-token-enforce')).toBe('service-hub-route-token-enforce');
  expect(profileProject('service-hub-auth')).toBe('service-hub-auth');
  expect(profileProject('harness')).toBe('service-hub');
  expect(profileProject('harness-auth')).toBe('service-hub-auth');
  expect(profileProject('harness-actions')).toBe('service-hub-actions');
});
