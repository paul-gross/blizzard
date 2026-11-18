import { type FieldTree, submit, type ValidationError } from '@angular/forms/signals';
import type { SaveOutcome } from './outcome';

const HUB = 'hub';

/**
 * A refused outcome as Signal Forms submission errors: each `fields` entry on its field, and the form line on the
 * root. A refused outcome always yields at least one error, so a refusal that names nothing still fails the submit
 * rather than reading as success. `undefined` for an ok outcome.
 */
export function toFormErrors<D>(
  outcome: SaveOutcome<D>,
  form: FieldTree<D>,
): ValidationError.WithOptionalFieldTree[] | undefined {
  if (outcome.ok) return undefined;
  const children = form as unknown as Record<string, FieldTree<unknown>>;
  const errors: ValidationError.WithOptionalFieldTree[] = [];
  for (const [key, message] of Object.entries(outcome.fields) as [string, string][]) {
    errors.push({ kind: HUB, fieldTree: children[key], message });
  }
  if (outcome.form !== null) errors.push({ kind: HUB, fieldTree: form, message: outcome.form });
  if (errors.length === 0) errors.push({ kind: HUB, fieldTree: form, message: 'Save failed.' });
  return errors;
}

/**
 * Runs Signal Forms' `submit`: it marks every field touched and runs `save` only when the form is valid. Afterwards
 * it focuses the first field in error, a rule's or the hub's, through that field's `focusBoundControl()`; errors on
 * the root alone focus nothing, and the form line carries them. Answers whether the save succeeded.
 */
export async function submitForm<D>(
  form: FieldTree<D>,
  save: (draft: D) => Promise<SaveOutcome<D>>,
): Promise<boolean> {
  const ok = await submit(form, async () => toFormErrors(await save(form().value()), form));
  if (!ok) {
    const first = form().errorSummary().find((error) => error.fieldTree !== form);
    first?.fieldTree().focusBoundControl();
  }
  return ok;
}
