import * as fs from 'node:fs';
import * as path from 'node:path';
import { parse } from 'smol-toml';
import { BLIZZARD_ROOT, TESTS_ROOT } from './env';

export const SERVICE_HUB_DIR = path.join(TESTS_ROOT, 'service', 'hub');

/** A profile as the folder walk finds it. The default profile is the folder's root. */
export interface ProfileFolder {
  /** The Playwright project: `service-hub`, or `service-hub-<subfolder>`. */
  project: string;
  /** `''` for the default profile, otherwise the subfolder's path below `service/hub/`. */
  name: string;
  dir: string;
  file: string;
}

/** The default profile and every subfolder holding a `profile.toml`, found without reading any of them. */
export function discoverProfiles(root: string = SERVICE_HUB_DIR): ProfileFolder[] {
  const profiles: ProfileFolder[] = [
    { project: 'service-hub', name: '', dir: root, file: path.join(root, 'profile.toml') },
  ];
  const walk = (dir: string): void => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const child = path.join(dir, entry.name);
      const file = path.join(child, 'profile.toml');
      if (fs.existsSync(file)) {
        const name = path.relative(root, child).split(path.sep).join('-');
        profiles.push({ project: `service-hub-${name}`, name, dir: child, file });
      }
      walk(child);
    }
  };
  if (fs.existsSync(root)) walk(root);
  return profiles;
}

export type TomlTable = Record<string, unknown>;

/** Parses a profile's TOML, integers as `bigint` so the checker can tell `1` from `1.0`. */
export function parseProfile(text: string): TomlTable {
  return parse(text, { integersAsBigInt: true }) as TomlTable;
}

export function readProfile(file: string): TomlTable {
  return fs.existsSync(file) ? parseProfile(fs.readFileSync(file, 'utf8')) : {};
}

interface ContractKey {
  path: string;
  type: string;
  deprecated: boolean;
  replaced_by: string | null;
}

let contractKeys: Map<string, ContractKey> | undefined;

function hubKeys(): Map<string, ContractKey> {
  if (contractKeys === undefined) {
    const document = JSON.parse(
      fs.readFileSync(path.join(BLIZZARD_ROOT, 'contracts', 'config', 'hub.json'), 'utf8'),
    ) as { keys: ContractKey[] };
    contractKeys = new Map(document.keys.map((key) => [key.path, key]));
  }
  return contractKeys;
}

function isTable(value: unknown): value is TomlTable {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && !(value instanceof Date);
}

function describe(value: unknown): string {
  if (typeof value === 'bigint') return 'integer';
  if (typeof value === 'number') return 'float';
  if (Array.isArray(value)) return 'array';
  if (isTable(value)) return 'table';
  return typeof value;
}

/** Whether a value's TOML shape is the contract's declared `type`. */
function matches(type: string, value: unknown): boolean {
  switch (type) {
    case 'string':
      return typeof value === 'string';
    case 'integer':
      return typeof value === 'bigint';
    case 'number':
      return typeof value === 'bigint' || typeof value === 'number';
    case 'boolean':
      return typeof value === 'boolean';
    case 'list of strings':
      return Array.isArray(value) && value.every((item) => typeof item === 'string');
    case 'table':
    case 'open table':
      return isTable(value);
    case 'array of tables':
      return Array.isArray(value) && value.every(isTable);
    default:
      return false;
  }
}

/**
 * The problems in a profile, each naming its key. The hub reads its configuration leniently by key, so a misspelled
 * setting would leave every test believing in a configuration the hub never ran; this check asks nothing of the hub.
 *
 * A profile flattens into the contract's own notation: tables join with `.`, and an array of tables flattens as
 * `name[].key`. A key typed `open table` admits any path beneath it.
 */
export function checkProfile(profile: TomlTable): string[] {
  const keys = hubKeys();
  const problems: string[] = [];
  const visit = (table: TomlTable, prefix: string): void => {
    for (const [name, value] of Object.entries(table)) {
      const keyPath = `${prefix}${name}`;
      const key = keys.get(keyPath);
      if (key === undefined) {
        problems.push(`${keyPath}: not a key the hub declares (contracts/config/hub.json)`);
        continue;
      }
      if (key.deprecated) {
        const replacement = key.replaced_by === null ? '' : `; use ${key.replaced_by}`;
        problems.push(`${keyPath}: deprecated${replacement}`);
        continue;
      }
      if (!matches(key.type, value)) {
        problems.push(`${keyPath}: declared ${key.type}, the profile has ${describe(value)}`);
        continue;
      }
      if (key.type === 'table') visit(value as TomlTable, `${keyPath}.`);
      if (key.type === 'array of tables') {
        for (const item of value as TomlTable[]) visit(item, `${keyPath}[].`);
      }
    }
  };
  visit(profile, '');
  return problems;
}

/** Every sweep the hub's config contract types a `mode` for, so a sweep the hub adds is held to the rule unedited. */
function sweepNames(): string[] {
  return [...hubKeys().keys()].flatMap((keyPath) => /^sweeps\.([^.]+)\.mode$/.exec(keyPath)?.[1] ?? []);
}

/**
 * The sweeps a profile leaves on a timer. Every harness hub runs its sweeps `stepped`, so a pass happens only when a
 * test asks for one; a sweep left unset runs `timed`, and a clock-driven pass makes a wait on the system race it.
 */
export function checkStepped(profile: TomlTable): string[] {
  const sweeps = isTable(profile.sweeps) ? profile.sweeps : {};
  return sweepNames().flatMap((name) => {
    const entry = sweeps[name];
    const mode = isTable(entry) ? entry.mode : undefined;
    if (mode === 'stepped') return [];
    return [mode === undefined ? `sweeps.${name}.mode: unset, so the sweep runs timed; set it to "stepped"` : `sweeps.${name}.mode: ${JSON.stringify(mode)}, not "stepped"`];
  });
}

/** Checks every profile in the package, not only those a run selected, and fails naming each bad key or timed sweep. */
export function assertProfilesSound(profiles: ProfileFolder[] = discoverProfiles()): void {
  const failures = profiles.flatMap((folder) => {
    const profile = readProfile(folder.file);
    return [...checkProfile(profile), ...checkStepped(profile).map((problem) => `${folder.project} ${problem}`)].map(
      (problem) => `${path.relative(TESTS_ROOT, folder.file)}: ${problem}`,
    );
  });
  if (failures.length > 0) {
    throw new Error(`profile check failed:\n${failures.join('\n')}`);
  }
}

/** Merges a profile over the table `init` wrote, table by table and key by key; other values replace. */
export function mergeProfile(base: TomlTable, profile: TomlTable): TomlTable {
  const merged: TomlTable = { ...base };
  for (const [name, value] of Object.entries(profile)) {
    const existing = merged[name];
    merged[name] = isTable(existing) && isTable(value) ? mergeProfile(existing, value) : value;
  }
  return merged;
}
