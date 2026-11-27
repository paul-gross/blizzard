import { disabled, schema, validate } from '@angular/forms/signals';
import { parseSettingText } from 'fleet/core';

import type { HubSettingDraft } from './hub-settings.domain';

/** A one-key edit's rules: while the default is chosen the text is disabled; otherwise it must read as a value for
 * the key, which holds `current` when the edit starts. */
export function hubSettingRules(current: () => unknown) {
  return schema<HubSettingDraft>((p) => {
    disabled(p.value, ({ valueOf }) => valueOf(p.useDefault));
    validate(p.value, ({ value, valueOf }) => {
      if (valueOf(p.useDefault)) return null;
      const parsed = parseSettingText(current(), value());
      return 'error' in parsed ? { kind: 'setting-value', message: parsed.error } : null;
    });
  });
}
