import { settingValueText } from 'fleet/core';

import type { HostRecordState } from '../core/host-record.domain';
import { type HostRecord, type HostRecordKind, recordRetired } from './host-records.domain';
import type { SettingBadge } from './settings.model';

/** A record state's badge: an active record green, a retired one grey, an unknown one raw. */
export function recordStateBadge(state: HostRecordState): SettingBadge {
  if (typeof state !== 'string') return { label: state.raw, tone: 'idle' };
  return state === 'active' ? { label: 'active', tone: 'running' } : { label: 'retired', tone: 'idle' };
}

/** A panel's heading for `kind`. */
export function recordsTitle(kind: HostRecordKind): string {
  return kind === 'workspace' ? 'Workspaces' : 'Subscriptions';
}

/** One stated key of a record, as its detail shows it. */
export interface RecordSettingVm {
  readonly key: string;
  readonly value: string;
}

/** Which verbs a record offers the session. */
export interface RecordVerbsVm {
  readonly edit: boolean;
  readonly retire: boolean;
  readonly enable: boolean;
}

/** One record as its panel shows it. */
export interface HostRecordVm {
  readonly key: string;
  readonly state: SettingBadge;
  readonly retired: boolean;
  readonly settings: readonly RecordSettingVm[];
  readonly verbs: RecordVerbsVm;
}

/** The verbs `record` offers: none but to the superuser; an edit only of a record with a revision; retire of an
 * active record and enable of a retired one. */
export function recordVerbs(record: HostRecord, superuser: boolean): RecordVerbsVm {
  const kept = superuser && record.revision !== null;
  const retired = recordRetired(record);
  return { edit: kept, retire: kept && record.state === 'active', enable: kept && retired };
}

/** `record` as its row shows it, its stated keys in key order. */
export function hostRecordVm(record: HostRecord, superuser: boolean): HostRecordVm {
  return {
    key: record.key,
    state: recordStateBadge(record.state),
    retired: recordRetired(record),
    settings: Object.keys(record.settings)
      .sort()
      .map((key) => ({ key, value: settingValueText(record.settings[key]) })),
    verbs: recordVerbs(record, superuser),
  };
}

/** Every record of a panel, active ones first and each group in key order. */
export function hostRecordVms(records: readonly HostRecord[] | undefined, superuser: boolean): readonly HostRecordVm[] {
  return [...(records ?? [])]
    .sort((a, b) => Number(recordRetired(a)) - Number(recordRetired(b)) || a.key.localeCompare(b.key))
    .map((record) => hostRecordVm(record, superuser));
}

/** The record `key` names in `records`, `null` once none does. */
export function hostRecordOf(records: readonly HostRecord[] | undefined, key: string | null): HostRecord | null {
  return key === null ? null : (records?.find((record) => record.key === key) ?? null);
}

/** The confirm prompt for retiring the `kind` record `key`. */
export function retirePrompt(kind: HostRecordKind, key: string) {
  return {
    heading: `Retire ${kind}`,
    message: `Retire ${kind} ${key}? It stays listed as retired, and Enable brings it back.`,
    confirmLabel: 'Retire',
    variant: 'danger' as const,
  };
}

/** `tree` while a create is open, and `null` otherwise. */
export function whileCreating<T>(creating: boolean, tree: T): T | null {
  return creating ? tree : null;
}
