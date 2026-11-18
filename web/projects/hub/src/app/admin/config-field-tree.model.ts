import type { FieldChangeView } from 'fleet';

import { type ConfigFieldOption, markIsImage } from './config-edit.model';

/** What the dialog is for: a create takes every field; an edit takes the editable ones and shows a "Will change"
 * diff; a replace takes whatever fields it is given. */
export type ConfigFieldTreeMode = 'create' | 'edit' | 'replace';

/** The controls a string-valued draft field can be drawn with. */
export type StringFieldKind = 'text' | 'password' | 'secret-text' | 'select' | 'number' | 'color' | 'mark';

/** The keys of `D` whose value is text. */
export type StringKeys<D> = { [K in keyof D & string]-?: D[K] extends string ? K : never }[keyof D & string];

interface FieldViewBase<D> {
  readonly label: string;
  /** Whether a create is the only place it can be set. */
  readonly createOnly?: boolean;
  /** Choices for a select or color field. */
  readonly options?: readonly ConfigFieldOption[];
  /** A choice is offered only when its kind matches this other field's current value. */
  readonly filterBy?: StringKeys<D>;
  /** The field is shown only while another field holds a given value. */
  readonly shownWhen?: { readonly key: StringKeys<D>; readonly value: string };
}

/**
 * How one field of the draft `D` is drawn: presentation only. The kind follows the field's value type — a checkbox for
 * a `boolean` field, any other kind for a `string` field — so the dialog needs no cast to pick a control. Whether a
 * field is required, or its text a number, is the record's own rules and translator, never this metadata.
 */
export type ConfigFieldView<D> = {
  [K in keyof D & string]-?: D[K] extends boolean
    ? FieldViewBase<D> & { readonly key: K; readonly kind: 'checkbox' }
    : D[K] extends string
      ? FieldViewBase<D> & { readonly key: K; readonly kind: StringFieldKind }
      : never;
}[keyof D & string];

function valueOf<D>(draft: D, key: string): unknown {
  return (draft as Record<string, unknown>)[key];
}

/** The fields a mode shows for `draft`: an edit leaves out the create-only ones, and a field whose `shownWhen` does
 * not hold is left out. */
export function shownFieldViews<D>(
  fields: readonly ConfigFieldView<D>[],
  mode: ConfigFieldTreeMode,
  draft: D,
): readonly ConfigFieldView<D>[] {
  return fields.filter(
    (field) =>
      !(mode === 'edit' && field.createOnly) && (!field.shownWhen || valueOf(draft, field.shownWhen.key) === field.shownWhen.value),
  );
}

/** The options a field offers: all of them, or — for a dependent field — those whose kind matches its parent's value. */
export function optionsOf<D>(field: ConfigFieldView<D>, draft: D): readonly ConfigFieldOption[] {
  return (field.options ?? []).filter((option) => !field.filterBy || option.kind === valueOf(draft, field.filterBy));
}

/** Every key whose choices hang on `key`, directly or through another dependent. */
export function dependentsOf<D>(fields: readonly ConfigFieldView<D>[], key: string): readonly string[] {
  const direct = fields.filter((field) => field.filterBy === key).map((field) => field.key as string);
  return direct.flatMap((dependent) => [dependent, ...dependentsOf(fields, dependent)]);
}

/** A `number` field's text compares by value, so "05" over a stored "5" is no change. */
function sameNumber(before: unknown, after: unknown): boolean {
  return (
    typeof before === 'string' &&
    typeof after === 'string' &&
    /^\d+$/.test(before.trim()) &&
    /^\d+$/.test(after.trim()) &&
    Number(before) === Number(after)
  );
}

function labelOf<D>(field: ConfigFieldView<D>, value: unknown): string | null {
  if (field.kind === 'mark') return typeof value === 'string' && markIsImage(value) ? 'Image' : null;
  if ((field.kind !== 'select' && field.kind !== 'color') || typeof value !== 'string') return null;
  return field.options?.find((option) => option.value === value)?.label ?? null;
}

/**
 * What an edit would change, as the "Will change" rows: the editable shown fields whose draft differs from the
 * `baseline` draft the edit started from. Text compares trimmed, blank reads as none, number text compares by value.
 * It is presentation — each record's edit translator decides what is sent.
 */
export function fieldChanges<D>(
  fields: readonly ConfigFieldView<D>[],
  baseline: D,
  draft: D,
): readonly FieldChangeView[] {
  const diff: FieldChangeView[] = [];
  for (const field of shownFieldViews(fields, 'edit', draft)) {
    const before = valueOf(baseline, field.key);
    const raw = valueOf(draft, field.key);
    const after = typeof raw === 'string' ? raw.trim() : raw;
    if (after === before || (field.kind === 'number' && sameNumber(before, after))) continue;
    const next = after === '' && field.kind !== 'select' ? null : after;
    diff.push({
      field: field.label,
      old: before === '' ? null : before,
      new: next,
      old_label: labelOf(field, before),
      new_label: labelOf(field, next),
    } as FieldChangeView);
  }
  return diff;
}
