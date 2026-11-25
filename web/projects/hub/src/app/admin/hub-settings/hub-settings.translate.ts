// bzh:frontend-domain-boundary — the hub settings wire read into the edit's domain.
import { type HubSettingRow, readRefusal, type SaveOutcome, settingValueText } from 'fleet';

import type { HubSettingBaseline, HubSettingDraft } from './hub-settings.domain';

/** What an edit of `row` starts from: the key, the settings read's revision, and the key's value, all from one read. */
export function toHubSettingBaseline(revision: number, row: HubSettingRow): HubSettingBaseline {
  return {
    id: row.key,
    revision,
    draft: { value: settingValueText(row.value), useDefault: row.source === 'default' },
    current: row.value,
  };
}

/** Every refusal not placed by a `settings-invalid` entry: a conflict, an unknown or not-live key, or anything else
 * goes on the form line, worded by its `detail`. */
export const HUB_SETTING_REFUSALS = { field: () => null, kinds: {} } as const;

const SETTINGS_INVALID = 'urn:blizzard:problem:settings-invalid';

function invalidEntries(error: unknown): Record<string, unknown> | null {
  if (typeof error !== 'object' || error === null) return null;
  const { type, errors } = error as { type?: unknown; errors?: unknown };
  if (type !== SETTINGS_INVALID || typeof errors !== 'object' || errors === null) return null;
  return errors as Record<string, unknown>;
}

/** A refused write of `key` as a {@link SaveOutcome}: a `settings-invalid` entry for the key goes on `value`. While
 * `useDefault` holds, every refusal goes on the form line instead — `value` is then disabled, and a disabled field
 * shows no error. */
export function hubSettingRefusal(error: unknown, key: string, useDefault: boolean): SaveOutcome<HubSettingDraft> {
  const entries = invalidEntries(error);
  const own = entries?.[key];
  if (entries && !useDefault && typeof own === 'string') {
    const others = Object.entries(entries)
      .filter(([name]) => name !== key)
      .map(([name, message]) => `${name}: ${String(message)}`);
    return { ok: false, fields: { value: own }, form: others.length > 0 ? others.join('; ') : null };
  }
  return readRefusal<HubSettingDraft>(error, HUB_SETTING_REFUSALS, 'Save failed.');
}
