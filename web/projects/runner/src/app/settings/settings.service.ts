import { type EditBaseline, parseSettingText, type SaveOutcome } from 'fleet';

import type { SettingDraft, SettingsTarget, SettingWrite } from './settings.domain';
import { injectSettingsWriteMutation } from './settings.mutations';
import { settingRefusal } from './settings.translate';

/** The one key an edit writes, and the value it held when the edit started. */
export interface SettingEditKey {
  readonly key: string;
  readonly current: unknown;
}

/** Writes one key of a settings record: domain in, a `SaveOutcome` out. */
export interface SettingsService {
  /** Sets the key to the value `after` reads as, against the baseline's revision as `If-Match`. */
  set(edit: SettingEditKey, before: EditBaseline<SettingDraft>, after: SettingDraft): Promise<SaveOutcome<SettingDraft>>;
  /** Resets the key to its default, against the baseline's revision as `If-Match`. */
  unset(edit: SettingEditKey, before: EditBaseline<SettingDraft>): Promise<SaveOutcome<SettingDraft>>;
  /** Whether a write of this record is in flight. */
  pending(): boolean;
}

/** The settings service over `target`'s write mutation. A save resolves once the mutation's invalidations settle;
 * a refusal is answered, never thrown — a revision conflict on the form line, never retried against a newer revision. */
export function injectSettingsService(target: () => SettingsTarget): SettingsService {
  const mutation = injectSettingsWriteMutation(target);
  const send = async (write: SettingWrite, revision: number): Promise<SaveOutcome<SettingDraft>> => {
    try {
      await mutation.mutateAsync({ target: target(), revision, write });
      return { ok: true };
    } catch (error) {
      return settingRefusal(error, write.key);
    }
  };
  return {
    set(edit, before, after) {
      const parsed = parseSettingText(edit.current, after.value);
      if ('error' in parsed) return Promise.resolve({ ok: false, fields: { value: parsed.error }, form: null });
      return send({ kind: 'set', key: edit.key, value: parsed.value }, before.revision);
    },
    unset(edit, before) {
      return send({ kind: 'unset', key: edit.key }, before.revision);
    },
    pending: () => mutation.isPending(),
  };
}
