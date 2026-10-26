import * as fs from 'node:fs';
import * as path from 'node:path';
import { expect, test } from '@playwright/test';
import { assertProfilesSound, checkProfile, checkStepped, discoverProfiles, parseProfile, readProfile } from '../profiles';

const fixture = (name: string): string => path.join(__dirname, 'profiles', `${name}.toml`);
const problemsIn = (name: string): string[] => checkProfile(readProfile(fixture(name)));

test('a sound profile passes', () => {
  expect(problemsIn('sound')).toEqual([]);
});

test('a misspelled key fails, naming the key', () => {
  expect(problemsIn('misspelled')).toEqual([expect.stringContaining('action_pas_seconds')]);
});

test('a deprecated key fails even though the hub still loads it, naming the key', () => {
  expect(problemsIn('deprecated')).toEqual([expect.stringContaining('work_source[].close')]);
});

test('a mistyped value fails, naming the key and the declared type', () => {
  expect(problemsIn('mistyped')).toEqual([expect.stringMatching(/action_pass_seconds.*integer/)]);
});

test('a float fails an integer key, and an integer passes a number key', () => {
  expect(checkProfile(parseProfile('action_pass_seconds = 1.5'))).toEqual([expect.stringContaining('action_pass_seconds')]);
  expect(checkProfile(parseProfile('[tracing]\nplatform_sample_ratio = 1'))).toEqual([]);
  expect(checkProfile(parseProfile('[tracing]\nplatform_sample_ratio = 0.5'))).toEqual([]);
});

test('a key typed as a table must be a table, and an open table admits any path beneath it', () => {
  expect(checkProfile(parseProfile('auth = 3'))).toEqual([expect.stringMatching(/^auth: declared table/)]);
  expect(checkProfile(parseProfile('[flags.deeper]\nanything = 1'))).toEqual([]);
});

test('an array of tables flattens as name[].key', () => {
  expect(checkProfile(parseProfile('[[work_source]]\nrepo = "o/n"\nrepro = "x"'))).toEqual([
    expect.stringContaining('work_source[].repro'),
  ]);
});

test('every committed profile in the package is sound', () => {
  for (const folder of discoverProfiles()) {
    expect(checkProfile(readProfile(folder.file)), folder.file).toEqual([]);
  }
});

test('the default profile is the folder’s root and every subfolder holding a profile.toml is a project', () => {
  const root = fs.mkdtempSync(path.join(process.env.BLIZZARD_TMPDIR ?? '/tmp', 'profiles-'));
  fs.writeFileSync(path.join(root, 'profile.toml'), '');
  fs.mkdirSync(path.join(root, 'auth'));
  fs.writeFileSync(path.join(root, 'auth', 'profile.toml'), '');
  fs.mkdirSync(path.join(root, 'plain'));
  expect(discoverProfiles(root).map((folder) => folder.project)).toEqual(['service-hub', 'service-hub-auth']);
  fs.rmSync(root, { recursive: true });
});

test('a profile fails the stepped rule naming each sweep left timed and each left unset', () => {
  const folder = path.join(__dirname, 'profiles', 'unstepped');
  const problems = checkStepped(readProfile(path.join(folder, 'profile.toml')));
  expect(problems).toHaveLength(2);
  expect(problems.join('\n')).toMatch(/sweeps\.forge_status\.mode.*timed/);
  expect(problems.join('\n')).toMatch(/sweeps\.egress\.mode.*unset/);
  expect(() => assertProfilesSound(discoverProfiles(folder).slice(0, 1))).toThrow(/sweeps\.forge_status\.mode[\s\S]*sweeps\.egress\.mode|sweeps\.egress\.mode[\s\S]*sweeps\.forge_status\.mode/);
});

test('every committed profile steps every sweep', () => {
  expect(() => assertProfilesSound()).not.toThrow();
});
