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

/** The config contract a profile is checked against: `hub.json` for a hub's, `host.json` for a runner host's. */
export type Contract = 'hub' | 'host';

const contractKeys = new Map<Contract, Map<string, ContractKey>>();

function keysOf(contract: Contract): Map<string, ContractKey> {
  let keys = contractKeys.get(contract);
  if (keys === undefined) {
    const document = JSON.parse(
      fs.readFileSync(path.join(BLIZZARD_ROOT, 'contracts', 'config', `${contract}.json`), 'utf8'),
    ) as { keys: ContractKey[] };
    keys = new Map(document.keys.map((key) => [key.path, key]));
    contractKeys.set(contract, keys);
  }
  return keys;
}

function hubKeys(): Map<string, ContractKey> {
  return keysOf('hub');
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
    case 'string or list of strings':
      return typeof value === 'string' || (Array.isArray(value) && value.every((item) => typeof item === 'string'));
    case 'table':
    case 'open table':
      return isTable(value);
    case 'array of tables':
      return Array.isArray(value) && value.every(isTable);
    default:
      return false;
  }
}

/** What a profile's strings may name as `${name}`, and the text each expands to. */
export type Placeholders = Record<string, string>;

/** The stack addresses a hub profile's strings may name, `${idp}` and `${forge}`, and the base URL each expands to. */
export interface StackUrls extends Placeholders {
  idp: string;
  forge: string;
}

const PLACEHOLDER = /\$\{([^}]*)\}/g;

/** Every `${…}` in a string value anywhere in the table, whatever the key's type: tables, arrays, and open tables alike. */
function placeholderProblems(value: unknown, keyPath: string, allowed: string[]): string[] {
  if (typeof value === 'string') {
    const named = allowed.map((name) => `\${${name}}`).join(' and ');
    return [...value.matchAll(PLACEHOLDER)].flatMap((match) =>
      allowed.includes(match[1] as string) ? [] : [`${keyPath}: \${${match[1]}} is not a placeholder; the profile may name ${named}`],
    );
  }
  if (Array.isArray(value)) return value.flatMap((item, index) => placeholderProblems(item, `${keyPath}[${index}]`, allowed));
  if (isTable(value)) return Object.entries(value).flatMap(([name, item]) => placeholderProblems(item, keyPath === '' ? name : `${keyPath}.${name}`, allowed));
  return [];
}

/** The profile with each named placeholder replaced in every string value; it holds no other once `checkProfile` passed. */
export function expandProfile(profile: TomlTable, urls: Placeholders): TomlTable {
  const expand = (value: unknown): unknown => {
    if (typeof value === 'string') return Object.entries(urls).reduce((text, [name, replacement]) => text.replaceAll(`\${${name}}`, replacement), value);
    if (Array.isArray(value)) return value.map(expand);
    if (isTable(value)) return Object.fromEntries(Object.entries(value).map(([name, item]) => [name, expand(item)]));
    return value;
  };
  return expand(profile) as TomlTable;
}

/**
 * The problems in a profile, each naming its key. The hub reads its configuration leniently by key, so a misspelled
 * setting would leave every test believing in a configuration the hub never ran; this check asks nothing of the hub.
 *
 * A profile flattens into the contract's own notation: tables join with `.`, and an array of tables flattens as
 * `name[].key`. A key typed `open table` admits any path beneath it. A string value may name the contract's
 * placeholders, `${idp}` and `${forge}` for a hub and the `placeholders` a host profile is given, and no other `${…}`,
 * wherever it sits.
 */
export function checkProfile(profile: TomlTable, options: { contract?: Contract; placeholders?: string[] } = {}): string[] {
  const contract = options.contract ?? 'hub';
  const keys = keysOf(contract);
  const problems: string[] = placeholderProblems(profile, '', options.placeholders ?? ['idp', 'forge']);
  const visit = (table: TomlTable, prefix: string): void => {
    for (const [name, value] of Object.entries(table)) {
      const keyPath = `${prefix}${name}`;
      const key = keys.get(keyPath);
      if (key === undefined) {
        problems.push(`${keyPath}: not a key the ${contract} declares (contracts/config/${contract}.json)`);
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
export function sweepNames(): string[] {
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

/** The identity key an array of tables' entries are matched by when a host profile merges over what the CLI wrote. */
const HOST_IDENTITY: Record<string, string> = { runner: 'name', subscription: 'slug', workspace: 'id' };

function mergeHostAt(base: TomlTable, profile: TomlTable, keyPath: string): TomlTable {
  const merged: TomlTable = { ...base };
  for (const [name, value] of Object.entries(profile)) {
    const here = keyPath === '' ? name : `${keyPath}.${name}`;
    const existing = merged[name];
    const identity = HOST_IDENTITY[here];
    if (identity !== undefined && Array.isArray(existing) && Array.isArray(value)) {
      const entries = [...(existing as TomlTable[])];
      for (const entry of value as TomlTable[]) {
        const at = entries.findIndex((candidate) => candidate[identity] === entry[identity]);
        if (at < 0) entries.push(entry);
        else entries[at] = mergeHostAt(entries[at] as TomlTable, entry, `${here}[]`);
      }
      merged[name] = entries;
    } else {
      merged[name] = isTable(existing) && isTable(value) ? mergeHostAt(existing, value, here) : value;
    }
  }
  return merged;
}

/**
 * Merges a host profile over the file the CLI wrote: tables merge key by key, `runner[]` entries match by `name`,
 * `subscription[]` by `slug` and `workspace[]` by `id`, and any other value replaces the old one.
 */
export function mergeHostProfile(base: TomlTable, profile: TomlTable): TomlTable {
  return mergeHostAt(base, profile, '');
}

/** Keys the CLI or the fixture owns on a host, which a profile may not set; `[]` stands for an array-of-tables entry. */
export const HOST_OWNED_KEYS = [
  'hub_url',
  'host_token',
  'hub_tokens',
  'host',
  'port',
  'db_url',
  'runner[].id',
  'runner[].runner_id',
  'runner[].token_env',
];

/** The owned keys a host profile sets, each named; the CLI and the fixture write them, so a profile cannot. */
export function checkOwnedKeys(profile: TomlTable): string[] {
  const problems: string[] = [];
  const visit = (table: TomlTable, prefix: string): void => {
    for (const [name, value] of Object.entries(table)) {
      const keyPath = `${prefix}${name}`;
      if (HOST_OWNED_KEYS.includes(keyPath)) problems.push(`${keyPath}: the CLI or the fixture owns it, so a profile cannot set it`);
      if (Array.isArray(value) && value.every(isTable)) for (const item of value as TomlTable[]) visit(item, `${keyPath}[].`);
      else if (isTable(value)) visit(value, `${keyPath}.`);
    }
  };
  visit(profile, '');
  return problems;
}
