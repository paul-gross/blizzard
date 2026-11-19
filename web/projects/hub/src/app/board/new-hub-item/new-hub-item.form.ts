import { required, schema, validate } from '@angular/forms/signals';

import type { NewHubItemDraft } from './new-hub-item.domain';

// An empty value is `required()`'s to report; this catches the whitespace-only one it lets through.
const blank = (value: string) => (value !== '' && value.trim() === '' ? { kind: 'required', message: 'Required' } : null);

// The frontend's own rules for a new hub item (`decision:ui-toolkit/validate-both`): all four fields are needed, and a
// title or body of only whitespace states nothing. Whether the project accepts work is left to the hub.
export const newHubItemRules = schema<NewHubItemDraft>((p) => {
  required(p.title);
  validate(p.title, ({ value }) => blank(value()));
  required(p.body);
  validate(p.body, ({ value }) => blank(value()));
  required(p.project);
  required(p.urgency);
});
