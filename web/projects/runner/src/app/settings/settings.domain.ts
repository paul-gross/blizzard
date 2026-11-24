import type { UnknownHostValue } from 'fleet';

/** Which settings record a read or a write names: the host's own, or one hosted runner's. */
export type SettingsTarget = { readonly kind: 'host' } | { readonly kind: 'runner'; readonly runnerId: string };

/** When a change to a key reaches the daemon reading it; an unknown lifetime is one this build cannot name. */
export type SettingLifetime = 'pass' | 'session' | 'boot' | 'immutable' | UnknownHostValue;

/** Where a key's value comes from: a written entry, or the default. */
export type SettingSource = 'entry' | 'default' | UnknownHostValue;

/** One key of a settings record, with the change that last wrote it. */
export interface SettingRow {
  readonly key: string;
  readonly value: unknown;
  readonly source: SettingSource;
  readonly lifetime: SettingLifetime;
  /** The revision that last wrote the key; `null` while it has never been written. */
  readonly revision: number | null;
  readonly writtenAt: string | null;
  readonly writtenBy: string | null;
}

/** A revision that failed to take hold: its rebuild's error, and when it failed. */
export interface FailedRebuild {
  readonly revision: number;
  readonly error: string;
  readonly since: string;
}

/** Which revision the daemon runs, since when, and the newest revision whose rebuild failed. */
export interface InForce {
  readonly revision: number | null;
  readonly since: string | null;
  readonly failed: FailedRebuild | null;
}

/** A settings record as its read reports it. `settingsError` names why its keys could not be resolved; the read
 * then carries no keys, never older values. */
export interface SettingsRecord {
  readonly revision: number;
  readonly inForce: InForce;
  readonly settings: readonly SettingRow[];
  readonly settingsError: string | null;
}

/** What a change did to its record. */
export type ChangeOp = 'seed' | 'create' | 'edit' | 'retire' | 'enable' | 'replace' | UnknownHostValue;

/** Which way a change came in. */
export type ChangeDoor = 'seed' | 'cli' | 'page' | 'api' | 'apply' | UnknownHostValue;

/** Which kind of host record a host change moved. */
export type RecordKind = 'host' | 'runner' | 'workspace' | 'subscription' | UnknownHostValue;

/** One key's value on one side of a change, and whether it was the default. */
export interface ChangeEntry {
  readonly value: unknown;
  readonly atDefault: boolean;
}

/** One key a change moved; `old` is `null` for a key the change first wrote. */
export interface KeyChange {
  readonly key: string;
  readonly old: ChangeEntry | null;
  readonly new: ChangeEntry;
}

/** One row of a record's change log. A host change names the record it moved; a runner's change has `record` `null`. */
export interface SettingsChange {
  readonly id: number;
  readonly recordedAt: string;
  readonly revision: number;
  readonly op: ChangeOp;
  readonly door: ChangeDoor;
  readonly actor: string;
  readonly diff: readonly KeyChange[];
  readonly record: { readonly kind: RecordKind; readonly key: string | null } | null;
}

/** One page of a change log, and the cursor of the next older page, `null` at the oldest. */
export interface SettingsChangesPage {
  readonly changes: readonly SettingsChange[];
  readonly nextBefore: number | null;
}

/** One key's write: set to a value, or reset to its default. */
export type SettingWrite =
  | { readonly kind: 'set'; readonly key: string; readonly value: unknown }
  | { readonly kind: 'unset'; readonly key: string };

/** A one-key edit as its form states it: the value as typed. */
export interface SettingDraft {
  readonly value: string;
}

/** Whether `row` offers an edit control: the session must be the superuser, the key's lifetime `pass` or
 * `session`, and the record not a runner its host has retired. */
export function canEditSetting(superuser: boolean, row: SettingRow, recordRetired: boolean): boolean {
  return superuser && !recordRetired && (row.lifetime === 'pass' || row.lifetime === 'session');
}

/** Whether the record holds a revision the daemon has not yet taken up, a state a failed rebuild also holds. */
export function revisionPending(record: SettingsRecord): boolean {
  return record.inForce.revision !== null && record.inForce.revision < record.revision;
}
