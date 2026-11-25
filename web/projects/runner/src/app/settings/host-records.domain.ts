import type { HostRecordState } from '../core/host-record.domain';

/** Which kind of host record a panel lists and a verb writes. */
export type HostRecordKind = 'workspace' | 'subscription';

/** One workspace or subscription record, as its panel holds it: its key — the workspace's id, the subscription's
 * slug — and its record's revision, state, and settings. */
export interface HostRecord {
  readonly kind: HostRecordKind;
  readonly key: string;
  readonly revision: number | null;
  readonly state: HostRecordState;
  readonly settings: Readonly<Record<string, unknown>>;
}

/** How a field's text reads as its key's value: a string, a whole number, a list of names, a workspace's
 * repositories one `name url` per line, or a string kept as typed across lines. */
export type RecordFieldType = 'text' | 'whole' | 'list' | 'repos' | 'multiline';

/** One key a create or an edit form states, named as its table names it. */
export interface RecordField {
  readonly name: string;
  readonly label: string;
  readonly type: RecordFieldType;
  /** Whether a create must state it. */
  readonly required: boolean;
  /** Whether only a create states it: the record's key, and a subscription's provider. */
  readonly immutable: boolean;
  /** Whether an edit refuses to return it to its default, as a subscription's `name` does. */
  readonly refusesDefault: boolean;
}

type FieldFlags = Partial<Pick<RecordField, 'required' | 'immutable' | 'refusesDefault'>>;

/** A field, its name and flags kept as literals so a translator can hold the table to its request types. */
function field<const N extends string, const T extends RecordFieldType, const F extends FieldFlags = Record<never, never>>(
  name: N,
  label: string,
  type: T,
  flags?: F,
): RecordField & { readonly name: N; readonly type: T } & F {
  return { name, label, type, required: false, immutable: false, refusesDefault: false, ...flags } as RecordField & { readonly name: N; readonly type: T } & F;
}

/** Every key the create request of each kind takes; an edit states those not immutable. */
export const RECORD_FIELDS = {
  workspace: [
    field('id', 'Id', 'text', { required: true, immutable: true }),
    field('root', 'Root', 'text'),
    field('provider', 'Provider', 'text'),
    field('envs', 'Envs', 'list'),
    field('max_environments', 'Max environments', 'whole'),
    field('repo', 'Repos (one "name url" per line)', 'repos'),
    field('base_branch', 'Base branch', 'text'),
    field('prompt', 'Prompt', 'multiline'),
    field('prompt_file', 'Prompt file', 'text'),
    field('prompt_package', 'Prompt package', 'text'),
  ],
  subscription: [
    field('slug', 'Slug', 'text', { required: true, immutable: true }),
    field('name', 'Name', 'text', { required: true, refusesDefault: true }),
    field('provider', 'Provider', 'text', { required: true, immutable: true }),
    field('credentials_path', 'Credentials path', 'text'),
    field('sample_interval_seconds', 'Sample interval (seconds)', 'whole'),
    field('usage_api_base', 'Usage API base', 'text'),
  ],
} as const satisfies Readonly<Record<HostRecordKind, readonly RecordField[]>>;

/** A create or an edit as its form states it: each field's text as typed, by its key's name. */
export type RecordDraft = Readonly<Record<string, string>>;

/** Whether a create or an edit of a record the form states. */
export type RecordFormMode = 'create' | 'edit';

/** The fields a form of `mode` shows for `kind`: a create every one, an edit all but the immutable. */
export function recordFields(kind: HostRecordKind, mode: RecordFormMode): readonly RecordField[] {
  const fields: readonly RecordField[] = RECORD_FIELDS[kind];
  return fields.filter((f) => mode === 'create' || !f.immutable);
}

/** One of a workspace's repositories: its name and its clone URL. */
export interface RecordRepo {
  readonly name: string;
  readonly url: string;
}

/** The value a field of `type` reads its text as ({@link parseRecordField}), blank text aside. */
export type RecordFieldValue<T extends RecordFieldType> = T extends 'whole'
  ? number
  : T extends 'list'
    ? string[]
    : T extends 'repos'
      ? RecordRepo[]
      : string;

function parseRepos(text: string): { value: RecordRepo[] } | { error: string } {
  const repos: RecordRepo[] = [];
  for (const line of text.split('\n').map((l) => l.trim()).filter((l) => l !== '')) {
    const parts = line.split(/\s+/);
    if (parts.length !== 2) return { error: 'Write each repository as its name and its URL.' };
    repos.push({ name: parts[0], url: parts[1] });
  }
  return { value: repos };
}

/** One field's text read as its key's value: `null` for blank text, or why the text reads as none. */
export function parseRecordField(field: RecordField, text: string): { value: unknown } | { error: string } {
  const trimmed = text.trim();
  if (trimmed === '') return { value: null };
  if (field.type === 'multiline') return { value: text };
  if (field.type === 'whole') return /^\d+$/.test(trimmed) ? { value: Number(trimmed) } : { error: 'Enter a whole number.' };
  if (field.type === 'list') return { value: trimmed.split(/[\s,]+/).filter((name) => name !== '') };
  if (field.type === 'repos') return parseRepos(trimmed);
  return { value: trimmed };
}

/** Why `text` cannot stand in `field` on a form of `mode`, or `null` while it can. */
export function recordFieldError(field: RecordField, mode: RecordFormMode, text: string): string | null {
  const parsed = parseRecordField(field, text);
  if ('error' in parsed) return parsed.error;
  if (parsed.value === null && (mode === 'create' ? field.required : field.refusesDefault)) return 'Required.';
  return null;
}

/** Whether `record` is retired, so it can be enabled and not retired. */
export function recordRetired(record: HostRecord): boolean {
  return record.state === 'retired';
}

/** A draft with every field of either kind blank: a form's model, which the open create or edit then seeds. */
export function blankRecordDraft(): RecordDraft {
  return Object.fromEntries([...RECORD_FIELDS.workspace, ...RECORD_FIELDS.subscription].map((f) => [f.name, '']));
}
