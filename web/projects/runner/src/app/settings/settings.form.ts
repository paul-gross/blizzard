import { schema, validate } from '@angular/forms/signals';

import { parseSettingText } from 'fleet';
import type { SettingDraft } from './settings.domain';

/** A one-key edit's rules: the text must read as a value for the key, which holds `current` when the edit starts. */
export function settingEditRules(current: () => unknown) {
  return schema<SettingDraft>((p) => {
    validate(p.value, ({ value }) => {
      const parsed = parseSettingText(current(), value());
      return 'error' in parsed ? { kind: 'setting-value', message: parsed.error } : null;
    });
  });
}
