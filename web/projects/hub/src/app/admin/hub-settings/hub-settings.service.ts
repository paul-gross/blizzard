import { computed, type Signal } from '@angular/core';
import { parseSettingText, type SaveOutcome } from 'fleet';

import type { HubSettingBaseline, HubSettingDraft } from './hub-settings.domain';
import { injectHubSettingsMutation } from './hub-settings.mutations';
import { hubSettingRefusal } from './hub-settings.translate';

/** Saves one hub setting's draft: domain in, a `SaveOutcome` out. */
export interface HubSettingsService {
  /** Whether a write is in flight. */
  readonly pending: Signal<boolean>;
  /** Writes the draft against the baseline it started from: the default choice unsets the key, otherwise the text is
   * sent as a value for it. The baseline's revision goes as `If-Match`. */
  edit(before: HubSettingBaseline, after: HubSettingDraft): Promise<SaveOutcome<HubSettingDraft>>;
}

/**
 * The hub settings service over the settings mutation. A save resolves only once the mutation's invalidations settle
 * (`bzh:frontend-mutation-settles-on-refresh`). A refusal is answered, never thrown, and nothing is retried.
 */
export function injectHubSettingsService(): HubSettingsService {
  const mutation = injectHubSettingsMutation();
  return {
    pending: computed(() => mutation.isPending()),
    async edit(before, after) {
      let value: unknown = null;
      if (!after.useDefault) {
        const parsed = parseSettingText(before.current, after.value);
        if ('error' in parsed) return { ok: false, fields: { value: parsed.error }, form: null };
        value = parsed.value;
      }
      try {
        await mutation.mutateAsync({ key: before.id, revision: before.revision, value, unset: after.useDefault });
        return { ok: true };
      } catch (error) {
        return hubSettingRefusal(error, before.id, after.useDefault);
      }
    },
  };
}
