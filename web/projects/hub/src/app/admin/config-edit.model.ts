import type { FieldChangeView } from 'fleet';

/** How a config form field is entered. `password` is a write-only value the board
 * never reads back. */
export type ConfigFieldKind = 'text' | 'password' | 'checkbox';

/** One field of a config record's form. */
export interface ConfigFieldDef {
  readonly key: string;
  readonly label: string;
  readonly kind: ConfigFieldKind;
  /** Whether a create needs a value for it. */
  readonly required?: boolean;
  /** Whether a create is the only place it can be set — a name is immutable. */
  readonly createOnly?: boolean;
}

/** A form's values by field key: text for `text`/`password`, a flag for `checkbox`. */
export type ConfigFormValues = Readonly<Record<string, string | boolean>>;

/** What an edit sends and shows: the changed fields as the PATCH body, and as the
 * "Will change" rows. */
export interface ConfigEdit {
  readonly body: Readonly<Record<string, string | boolean | null>>;
  readonly diff: readonly FieldChangeView[];
}

/** The fields an edit can change — every field a create alone sets is left out. */
export function editableFields(fields: readonly ConfigFieldDef[]): readonly ConfigFieldDef[] {
  return fields.filter((field) => !field.createOnly);
}

/** A record's shown values as form values, one per field. */
export function formValuesOf(fields: readonly ConfigFieldDef[], record: object): ConfigFormValues {
  const source = record as Record<string, unknown>;
  const values: Record<string, string | boolean> = {};
  for (const field of fields) {
    const value = source[field.key];
    values[field.key] = field.kind === 'checkbox' ? value === true : typeof value === 'string' ? value : '';
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
 * answers a refusal with a 422. The same fields are the patch body and the diff, so
 * what the operator reviews is exactly what is sent.
 */
export function configEdit(
  fields: readonly ConfigFieldDef[],
  shown: ConfigFormValues,
  form: ConfigFormValues,
): ConfigEdit {
  const body: Record<string, string | boolean | null> = {};
  const diff: FieldChangeView[] = [];
  for (const field of editableFields(fields)) {
    const before = shown[field.key];
    const raw = form[field.key];
    const after = typeof raw === 'string' ? raw.trim() : raw;
    if (after === before) continue;
    const next = after === '' ? null : after;
    body[field.key] = next;
    diff.push({
      field: field.label,
      old: before === '' ? null : before,
      new: next,
    });
  }
  return { body, diff };
}

/** The labels of the required fields `form` leaves empty. */
export function missingRequired(fields: readonly ConfigFieldDef[], form: ConfigFormValues): readonly string[] {
  return fields
    .filter((field) => field.required)
    .filter((field) => typeof form[field.key] !== 'string' || (form[field.key] as string).trim() === '')
    .map((field) => field.label);
}

/** The body a create sends: every non-empty field, text trimmed. */
export function createBody(
  fields: readonly ConfigFieldDef[],
  form: ConfigFormValues,
): Record<string, string | boolean> {
  const body: Record<string, string | boolean> = {};
  for (const field of fields) {
    const value = form[field.key];
    if (typeof value === 'boolean') {
      body[field.key] = value;
    } else if (value !== undefined && value.trim() !== '') {
      body[field.key] = value.trim();
    }
  }
  return body;
}

/** A shown record's form values, or `null` with no record loaded. */
export function shownFormValues(
  fields: readonly ConfigFieldDef[],
  record: object | null | undefined,
): ConfigFormValues | null {
  return record ? formValuesOf(fields, record) : null;
}
