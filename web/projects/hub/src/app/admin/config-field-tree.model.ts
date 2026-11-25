import type { FieldChangeView } from 'fleet';

import { type ConfigFieldOption, type MarkImageSource, markIsImage } from './config-edit.model';

/** What the dialog is for: a create takes every field; an edit takes the editable ones and shows a "Will change"
 * diff; a replace takes whatever fields it is given. */
export type ConfigFieldTreeMode = 'create' | 'edit' | 'replace';

/** The controls a string-valued draft field can be drawn with. */
export type StringFieldKind = 'text' | 'password' | 'secret-text' | 'select' | 'number' | 'color' | 'mark';

/** The keys of `D` whose value is text. */
export type StringKeys<D> = { [K in keyof D & string]-?: D[K] extends string ? K : never }[keyof D & string];

/** The keys of `D` whose value is a flag. */
export type BooleanKeys<D> = { [K in keyof D & string]-?: D[K] extends boolean ? K : never }[keyof D & string];

/** A `number` or `text` field's explicit default: the draft flag that holds the choice, and what the choice is called. While the
 * flag holds, the field reads as the default, whatever its text. */
export interface DefaultChoice<D> {
  readonly key: BooleanKeys<D>;
  readonly label: string;
}

interface FieldViewBase<D> {
  readonly label: string;
  /** Whether a create is the only place it can be set. */
  readonly createOnly?: boolean;
  /** Maximum decoded image bytes for a mark upload. */
  readonly maxImageBytes?: number;
  readonly markImageSource?: MarkImageSource;
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
      ?
          | (FieldViewBase<D> & { readonly key: K; readonly kind: Exclude<StringFieldKind, 'number' | 'text'> })
          | (FieldViewBase<D> & { readonly key: K; readonly kind: 'number' | 'text'; readonly orDefault?: DefaultChoice<D> })
      : never;
}[keyof D & string];

function valueOf<D>(draft: D, key: string): unknown {
  return (draft as Record<string, unknown>)[key];
}

/** A field's explicit default choice, or `null` when it offers none. */
export function defaultChoiceOf<D>(field: ConfigFieldView<D>): DefaultChoice<D> | null {
  return ((field.kind === 'number' || field.kind === 'text') && field.orDefault) || null;
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
  if (field.kind === 'mark') return typeof value === 'string' && markIsImage(value, field.markImageSource) ? 'Image' : null;
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
    const choice = defaultChoiceOf(field);
    if (choice !== null && (valueOf(baseline, choice.key) === true || valueOf(draft, choice.key) === true)) {
      const change = defaultChange(field, choice, baseline, draft);
      if (change !== null) diff.push(change);
      continue;
    }
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
    });
  }
  return diff;
}

/** A defaulted field's change, when either side chose the default: none while both do, else the default named
 * by its label on its side and the stated text on the other. */
function defaultChange<D>(
  field: ConfigFieldView<D>,
  choice: DefaultChoice<D>,
  baseline: D,
  draft: D,
): FieldChangeView | null {
  const wasDefault = valueOf(baseline, choice.key) === true;
  const isDefault = valueOf(draft, choice.key) === true;
  if (wasDefault && isDefault) return null;
  const stated = (side: D): string | null => {
    const text = valueOf(side, field.key);
    return typeof text === 'string' && text.trim() !== '' ? text.trim() : null;
  };
  return {
    field: field.label,
    old: wasDefault ? null : stated(baseline),
    new: isDefault ? null : stated(draft),
    old_label: wasDefault ? choice.label : null,
    new_label: isDefault ? choice.label : null,
  };
}
