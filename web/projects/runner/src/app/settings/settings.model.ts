import { settingValueText, type Tone, type UnknownHostValue } from 'fleet';

import type { HostRunner } from '../core/host-runner.domain';
import {
  canEditSetting,
  type FailedRebuild,
  type RecordKind,
  type SettingLifetime,
  type SettingRow,
  type SettingsChange,
  type SettingsChangesPage,
  type SettingsRecord,
  type SettingSource,
  type SettingsTarget,
} from './settings.domain';

/** One settings record the page shows: the host's, or one hosted runner's. */
export interface SettingsRecordRef {
  /** The record's id — `host`, or the runner's id — and the prefix of its test ids. */
  readonly id: string;
  readonly title: string;
  readonly target: SettingsTarget;
  /** Whether the record is a runner its host has retired, whose keys take no edit. */
  readonly retired: boolean;
}

/** The host's record, then one per hosted runner with an id, the Runner filter's alone when it names one. A runner
 * with no id has no settings record to read. */
export function settingsRecords(runners: readonly HostRunner[], runnerFilter: string | null): readonly SettingsRecordRef[] {
  const host: SettingsRecordRef = { id: 'host', title: 'Host', target: { kind: 'host' }, retired: false };
  const hosted = runners.flatMap((runner): SettingsRecordRef[] =>
    runner.runner_id === null || (runnerFilter !== null && runner.name !== runnerFilter)
      ? []
      : [
          {
            id: runner.runner_id,
            title: runner.name,
            target: { kind: 'runner', runnerId: runner.runner_id },
            retired: runner.state === 'retired',
          },
        ],
  );
  return [host, ...hosted];
}

function rawOr<T extends string>(value: T | UnknownHostValue): string {
  return typeof value === 'string' ? value : value.raw;
}

/** A label and its tone. */
export interface SettingBadge {
  readonly label: string;
  readonly tone: Tone;
}

/** A lifetime's badge: a live one green, `session` amber, `boot` and `immutable` grey, an unknown one raw. */
export function lifetimeBadge(lifetime: SettingLifetime): SettingBadge {
  if (typeof lifetime !== 'string') return { label: lifetime.raw, tone: 'idle' };
  switch (lifetime) {
    case 'pass':
      return { label: 'pass', tone: 'running' };
    case 'session':
      return { label: 'session', tone: 'waiting' };
    case 'boot':
    case 'immutable':
      return { label: lifetime, tone: 'idle' };
    default:
      return { label: String(lifetime), tone: 'idle' };
  }
}

/** One key as its row shows it. */
export interface SettingRowVm {
  readonly key: string;
  /** The key's last segment, shown under its table's heading. */
  readonly name: string;
  readonly value: string;
  readonly source: string;
  readonly lifetime: SettingBadge;
  readonly writtenAt: string | null;
  readonly writtenBy: string | null;
  readonly editable: boolean;
}

/** One table of a record's keys: the dotted path before each key's last segment, `''` for top-level keys. */
export interface SettingGroupVm {
  readonly table: string;
  readonly rows: readonly SettingRowVm[];
}

function tableOf(key: string): string {
  const dot = key.lastIndexOf('.');
  return dot < 0 ? '' : key.slice(0, dot);
}

function rowVm(row: SettingRow, editable: boolean): SettingRowVm {
  const table = tableOf(row.key);
  return {
    key: row.key,
    name: table === '' ? row.key : row.key.slice(table.length + 1),
    value: settingValueText(row.value),
    source: rawOr<SettingSource & string>(row.source),
    lifetime: lifetimeBadge(row.lifetime),
    writtenAt: row.writtenAt,
    writtenBy: row.writtenBy,
    editable,
  };
}

/** `record`'s keys grouped by table, top-level keys first and each table in the order its first key arrives; each
 * key editable per {@link canEditSetting}. */
export function settingGroups(record: SettingsRecord | undefined, superuser: boolean, retired: boolean): readonly SettingGroupVm[] {
  const groups = new Map<string, SettingRowVm[]>([['', []]]);
  for (const row of record?.settings ?? []) {
    const table = tableOf(row.key);
    const rows = groups.get(table) ?? [];
    rows.push(rowVm(row, canEditSetting(superuser, row, retired)));
    groups.set(table, rows);
  }
  return [...groups].filter(([, rows]) => rows.length > 0).map(([table, rows]) => ({ table, rows }));
}

/** What the `in_force` line says. `pending` is the written revision not yet in force, `null` when none waits or
 * when it is the revision whose rebuild failed. */
export interface InForceLineVm {
  readonly running: number | null;
  readonly since: string | null;
  readonly pending: number | null;
  readonly failed: FailedRebuild | null;
}

/** `record`'s `in_force` line, `null` before the record is read. */
export function inForceLine(record: SettingsRecord | undefined): InForceLineVm | null {
  if (record === undefined) return null;
  const { revision, since, failed } = record.inForce;
  const behind = revision === null || revision < record.revision;
  const pending = behind && failed?.revision !== record.revision && record.revision > 0 ? record.revision : null;
  return { running: revision, since, pending, failed };
}

/** The row of `key` in `record`, `null` once no row holds it. */
export function settingRowOf(record: SettingsRecord | undefined, key: string | null): SettingRow | null {
  return key === null ? null : (record?.settings.find((row) => row.key === key) ?? null);
}

/** One moved key of a change as its row shows it. */
export interface KeyChangeVm {
  readonly key: string;
  readonly old: string;
  readonly new: string;
}

/** One change row as History shows it. */
export interface SettingsChangeVm {
  readonly id: number;
  readonly revision: number;
  readonly op: string;
  readonly door: string;
  readonly actor: string;
  readonly at: string;
  /** The host record the change moved — `workspace ws-a` — or `null` for the record the log is of. */
  readonly record: string | null;
  readonly diff: readonly KeyChangeVm[];
}

function entryText(entry: { value: unknown; atDefault: boolean } | null): string {
  if (entry === null) return '—';
  return entry.atDefault ? `${settingValueText(entry.value)} (default)` : settingValueText(entry.value);
}

function recordLabel(record: { kind: RecordKind; key: string | null } | null): string | null {
  if (record === null || record.kind === 'host') return null;
  const kind = rawOr<RecordKind & string>(record.kind);
  return record.key === null ? kind : `${kind} ${record.key}`;
}

function changeVm(change: SettingsChange): SettingsChangeVm {
  return {
    id: change.id,
    revision: change.revision,
    op: rawOr(change.op),
    door: rawOr(change.door),
    actor: change.actor,
    at: change.recordedAt,
    record: recordLabel(change.record),
    diff: change.diff.map((d) => ({ key: d.key, old: entryText(d.old), new: entryText(d.new) })),
  };
}

/** Every loaded page's change rows, newest first. */
export function settingsChanges(pages: readonly SettingsChangesPage[] | undefined): readonly SettingsChangeVm[] {
  return (pages ?? []).flatMap((page) => page.changes.map(changeVm));
}

/** `tree` while an edit is under way — while `baseline` is held — and `null` otherwise. */
export function whileEditing<T>(baseline: unknown, tree: T): T | null {
  return baseline === null ? null : tree;
}
