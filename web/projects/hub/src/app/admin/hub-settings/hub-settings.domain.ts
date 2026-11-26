// The hub settings domain: what an edit of one setting holds. No wire and no Angular.
import type { EditBaseline } from 'fleet';

/** What the edit dialog holds: the value as typed, and whether the key takes its default instead. */
export interface HubSettingDraft {
  value: string;
  useDefault: boolean;
}

/** What an edit of one setting starts from: the key as `id`, the settings revision it was read at, the draft, and the
 * key's value at that same read, which stands in for its type when the typed text is read. */
export interface HubSettingBaseline extends EditBaseline<HubSettingDraft> {
  readonly current: unknown;
}

import type { ConfigFieldChange } from '../config-history.domain';

export interface UnknownHubSettingValue { readonly kind: 'unknown'; readonly raw: string }
export type HubSettingLifetime = 'pass' | 'session' | 'boot' | 'immutable' | UnknownHubSettingValue;
export type HubSettingSource = 'entry' | 'default' | UnknownHubSettingValue;
export type HubSettingsOperation = 'seed' | 'edit' | 'create' | 'retire' | 'enable' | UnknownHubSettingValue;
export type HubSettingsDoor = 'seed' | 'cli' | 'page' | 'api' | 'apply' | UnknownHubSettingValue;

export function hubSettingValueText(value: string | UnknownHubSettingValue): string {
  return typeof value === 'string' ? value : value.raw;
}

export interface HubSettingRow {
  readonly key: string;
  readonly value: unknown;
  readonly lifetime: HubSettingLifetime;
  readonly source: HubSettingSource;
  readonly revision: number | null;
  readonly written_at: string | null;
  readonly written_by: string | null;
}

export interface HubSettings {
  readonly revision: number;
  readonly settings: readonly HubSettingRow[];
}

export interface HubSettingsChange {
  readonly id: number;
  readonly revision: number;
  readonly op: HubSettingsOperation;
  readonly door: HubSettingsDoor;
  readonly actor: string;
  readonly recorded_at: string;
  readonly apply_id: string | null;
  readonly diff: readonly ConfigFieldChange[];
}

export interface HubSettingsHistoryPage {
  readonly changes: readonly HubSettingsChange[];
  readonly next_before: number | null;
}
