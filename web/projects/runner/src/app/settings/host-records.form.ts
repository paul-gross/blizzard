import { type FieldTree, schema, validateTree } from '@angular/forms/signals';

import type { RecordDraft, RecordField, RecordFormMode } from './host-records.domain';
import { recordFieldError } from './host-records.translate';

/** A create's or an edit's rules: each field's text must read as its key's value, and each required key be stated.
 * Judged over the whole draft, since which fields the form shows is the open form's. */
export function recordFormRules(fields: () => readonly RecordField[], mode: () => RecordFormMode) {
  return schema<RecordDraft>((p) => {
    validateTree(p, ({ value, fieldTree }) => {
      const draft = value();
      const children = fieldTree as unknown as Record<string, FieldTree<string>>;
      return fields().flatMap((f) => {
        const message = recordFieldError(f, mode(), draft[f.name] ?? '');
        return message === null || children[f.name] === undefined ? [] : [{ kind: 'record-field', fieldTree: children[f.name], message }];
      });
    });
  });
}
