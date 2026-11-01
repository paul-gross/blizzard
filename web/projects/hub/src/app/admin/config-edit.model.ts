import type { FieldChangeView } from 'fleet';

/** How a config form field is entered. `password` is a write-only value the board
 * never reads back; `number` is a whole number, entered as text and sent as a number. */
export type ConfigFieldKind = 'text' | 'password' | 'checkbox' | 'select' | 'number';

/** One choice of a select field. A disabled choice is shown but cannot be picked. */
export interface ConfigFieldOption {
  readonly value: string;
  readonly label: string;
  readonly kind?: string;
  readonly disabled?: boolean;
}

/** One field of a config record's form. `K` is the set of keys the form may name — a
 * list typed against a generated model fails to build when a wire field is renamed. */
export interface ConfigFieldDef<K extends string = string> {
  readonly key: K;
  readonly label: string;
  readonly kind: ConfigFieldKind;
  /** Whether a create needs a value for it. */
  readonly required?: boolean;
  /** Whether a create is the only place it can be set — a name is immutable. */
  readonly createOnly?: boolean;
  /** Choices for a select field; an empty list leaves its required value unpickable. */
  readonly options?: readonly ConfigFieldOption[];
  /** A choice is offered only when its kind matches this other field's current value. */
  readonly filterBy?: string;
  /** The field is shown only while another field holds a given value. A hidden field is
   * neither required nor sent. */
  readonly shownWhen?: { readonly key: string; readonly value: string };
}

/** A form's values by field key: text for `text`/`password`, a flag for `checkbox`. */
export type ConfigFormValues = Readonly<Record<string, string | boolean>>;

/** What an edit sends and shows: the changed fields as the PATCH body, and as the
 * "Will change" rows. */
export interface ConfigEdit {
  readonly body: Readonly<Record<string, string | number | boolean | null>>;
  readonly diff: readonly FieldChangeView[];
}

/** The fields an edit can change — every field a create alone sets is left out. */
export function editableFields(fields: readonly ConfigFieldDef[]): readonly ConfigFieldDef[] {
  return fields.filter((field) => !field.createOnly);
}

/** The fields `form` shows — every field but one whose {@link ConfigFieldDef.shownWhen}
 * does not hold. */
export function shownFields<F extends ConfigFieldDef>(fields: readonly F[], form: ConfigFormValues): readonly F[] {
  return fields.filter((field) => !field.shownWhen || form[field.shownWhen.key] === field.shownWhen.value);
}

/** The labels of the shown `number` fields whose text is neither blank nor a whole number. */
export function invalidNumbers(fields: readonly ConfigFieldDef[], form: ConfigFormValues): readonly string[] {
  return shownFields(fields, form)
    .filter((field) => field.kind === 'number')
    .filter((field) => {
      const value = form[field.key];
      return typeof value === 'string' && value.trim() !== '' && !/^\d+$/.test(value.trim());
    })
    .map((field) => field.label);
}

/** A record's shown values as form values, one per field. */
export function formValuesOf(fields: readonly ConfigFieldDef[], record: object): ConfigFormValues {
  const source = record as Record<string, unknown>;
  const values: Record<string, string | boolean> = {};
  for (const field of fields) {
    const value = source[field.key];
    values[field.key] =
      field.kind === 'checkbox'
        ? value === true
        : typeof value === 'string'
          ? value
          : typeof value === 'number'
            ? String(value)
            : '';
  }
  return values;
}

/** An empty form for `fields`. */
export function emptyFormValues(fields: readonly ConfigFieldDef[]): ConfigFormValues {
  return formValuesOf(fields, {});
}

/**
 * Compares `form` to the values `shown` and yields only what changed. A cleared text
 * input is sent as `null` — whether the field may be null is the hub's call, and it
 * answers a refusal with a 422. A `number` field is sent as a number. A field `form`
 * hides is never sent. The same fields are the patch body and the diff, so
 * what the operator reviews is exactly what is sent.
 */
export function configEdit(
  fields: readonly ConfigFieldDef[],
  shown: ConfigFormValues,
  form: ConfigFormValues,
): ConfigEdit {
  const body: Record<string, string | number | boolean | null> = {};
  const diff: FieldChangeView[] = [];
  for (const field of shownFields(editableFields(fields), form)) {
    const before = shown[field.key];
    const raw = form[field.key];
    const after = typeof raw === 'string' ? raw.trim() : raw;
    if (after === before || sameNumber(field, before, after)) continue;
    const next = after === '' && field.kind !== 'select' ? null : after;
    body[field.key] = field.kind === 'number' && typeof next === 'string' ? Number(next) : next;
    diff.push({
      field: field.label,
      old: before === '' ? null : before,
      new: next,
      old_label: optionLabel(field, before),
      new_label: optionLabel(field, next),
    });
  }
  return { body, diff };
}

/** A `number` field's text compares by value, so "05" over a stored "5" is no change. */
function sameNumber(field: ConfigFieldDef, before: unknown, after: unknown): boolean {
  return (
    field.kind === 'number' &&
    typeof before === 'string' &&
    typeof after === 'string' &&
    /^\d+$/.test(before) &&
    /^\d+$/.test(after) &&
    Number(before) === Number(after)
  );
}

function optionLabel(field: ConfigFieldDef, value: string | boolean | null): string | null {
  if (field.kind !== 'select' || typeof value !== 'string') return null;
  return field.options?.find((option) => option.value === value)?.label ?? null;
}

/** The labels of the shown required fields `form` leaves empty. */
export function missingRequired(fields: readonly ConfigFieldDef[], form: ConfigFormValues): readonly string[] {
  return shownFields(fields, form)
    .filter((field) => field.required)
    .filter((field) => typeof form[field.key] !== 'string' || (form[field.key] as string).trim() === '')
    .map((field) => field.label);
}

/** The body a create sends: every shown non-empty field, text trimmed. A `number`
 * field stays text here; its record's create request converts it. */
export function createBody(
  fields: readonly ConfigFieldDef[],
  form: ConfigFormValues,
): Record<string, string | boolean> {
  const body: Record<string, string | boolean> = {};
  for (const field of shownFields(fields, form)) {
    const value = form[field.key];
    if (typeof value === 'boolean') {
      body[field.key] = value;
    } else if (value !== undefined && value.trim() !== '') {
      body[field.key] = value.trim();
    }
  }
  return body;
}

/** One text field of a create body — `''` when the form left it empty. */
export function createText(body: Readonly<Record<string, string | boolean>>, key: string): string {
  const value = body[key];
  return typeof value === 'string' ? value : '';
}

/** A shown record's form values, or `null` with no record loaded. */
export function shownFormValues(
  fields: readonly ConfigFieldDef[],
  record: object | null | undefined,
): ConfigFormValues | null {
  return record ? formValuesOf(fields, record) : null;
}
