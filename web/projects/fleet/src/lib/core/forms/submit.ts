import { signal } from '@angular/core';
import { type FieldTree, type SchemaPath, schema, submit, validateTree, type ValidationError } from '@angular/forms/signals';
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

/** Apply the schema to the form and clear this state when starting a new draft. */
export function createRefusalState<D>() {
  const current = signal<{ outcome: SaveOutcome<D>; draft: D; dismissed: Set<string> } | null>(null);
  return {
    clear: () => current.set(null),
    set: (outcome: SaveOutcome<D>, draft: D) => current.set({ outcome, draft, dismissed: new Set() }),
    schema: schema<D>((path) => {
      validateTree(path as SchemaPath<D>, ({ value, fieldTree }) => {
        const saved = current();
        if (!saved || saved.outcome.ok) return [];
        const errors = toFormErrors(saved.outcome, fieldTree as FieldTree<D>) ?? [];
        const now = value() as Record<string, unknown>;
        const before = saved.draft as Record<string, unknown>;
        const children = fieldTree as unknown as Record<string, FieldTree<unknown>>;
        const fields = saved.outcome.fields;
        return errors.filter((error) => {
          if (error.fieldTree === fieldTree) return true;
          const key = Object.keys(fields).find((k) => children[k] === error.fieldTree);
          if (key === undefined) return false;
          if (!Object.is(now[key], before[key])) saved.dismissed.add(key);
          return !saved.dismissed.has(key);
        });
      });
    }),
  };
}

export type RefusalState<D> = ReturnType<typeof createRefusalState<D>>;

/**
 * Marks fields touched and runs `save` only when local validation passes. Apply the refusal state's schema to the form
 * so retries replace previous errors. An alternative action can disregard local errors unrelated to it. Focuses the first field
 * in error; root errors focus nothing. Answers whether the save succeeded.
 */
export async function submitForm<D>(
  form: FieldTree<D>,
  save: (draft: D) => Promise<SaveOutcome<D>>,
  options: { ignoreLocalErrors?: boolean; refusalState: RefusalState<D>; isCurrent?: () => boolean },
): Promise<boolean> {
  if (form().submitting()) return false;
  options.refusalState.clear();
  let locallyInvalid = false;
  let refused = false;
  let refusal: ValidationError.WithOptionalFieldTree[] | undefined;
  const submitted = await submit(form, {
    ignoreValidators: 'all',
    action: async () => {
      locallyInvalid = !options.ignoreLocalErrors && form().errorSummary().some((error) => error.kind !== HUB);
      if (locallyInvalid) return undefined;
      const draft = form().value();
      const outcome = await save(draft);
      if (options.isCurrent && !options.isCurrent()) return undefined;
      refusal = toFormErrors(outcome, form);
      refused = !!refusal;
      options.refusalState.set(outcome, draft);
      return undefined;
    },
  });
  if (options.isCurrent && !options.isCurrent()) return false;
  const ok = submitted && !locallyInvalid && !refused;
  if (!ok) {
    const first = locallyInvalid
      ? form().errorSummary().find((error) => error.fieldTree !== form && error.kind !== HUB)?.fieldTree
      : form().errorSummary().find((error) => error.fieldTree !== form && error.kind === HUB)?.fieldTree;
    first?.().focusBoundControl();
  }
  return ok;
}
