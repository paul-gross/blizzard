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

function field(name: string, label: string, type: RecordFieldType, flags: Partial<Pick<RecordField, 'required' | 'immutable' | 'refusesDefault'>> = {}): RecordField {
  return { name, label, type, required: false, immutable: false, refusesDefault: false, ...flags };
}

/** Every key the create request of each kind takes; an edit states those not immutable. */
export const RECORD_FIELDS: Readonly<Record<HostRecordKind, readonly RecordField[]>> = {
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
};

/** A create or an edit as its form states it: each field's text as typed, by its key's name. */
export type RecordDraft = Readonly<Record<string, string>>;

/** Whether a create or an edit of a record the form states. */
export type RecordFormMode = 'create' | 'edit';

/** The fields a form of `mode` shows for `kind`: a create every one, an edit all but the immutable. */
export function recordFields(kind: HostRecordKind, mode: RecordFormMode): readonly RecordField[] {
  return RECORD_FIELDS[kind].filter((f) => mode === 'create' || !f.immutable);
}

/** Whether `record` is retired, so it can be enabled and not retired. */
export function recordRetired(record: HostRecord): boolean {
  return record.state === 'retired';
}

/** A draft with every field of either kind blank: a form's model, which the open create or edit then seeds. */
export function blankRecordDraft(): RecordDraft {
  return Object.fromEntries([...RECORD_FIELDS.workspace, ...RECORD_FIELDS.subscription].map((f) => [f.name, '']));
}
