import { hidden, type SchemaPathTree } from '@angular/forms/signals';

import type { ConfigFieldView } from './config-field-tree.model';

/**
 * Applied inside a record's rules: every field whose metadata carries a `shownWhen` is hidden under the opposite
 * condition, so the dialog's visibility and the form's validity read one declaration. A hidden field adds no
 * validation to its parent.
 */
export function applyShownWhen<D>(path: SchemaPathTree<D>, fields: readonly ConfigFieldView<D>[]): void {
  const children = path as unknown as Record<string, SchemaPathTree<unknown>>;
  for (const field of fields) {
    const when = field.shownWhen;
    if (!when) continue;
    hidden(children[field.key], {
      when: ({ valueOf }) => valueOf(children[when.key] as never) !== when.value,
    });
  }
}
