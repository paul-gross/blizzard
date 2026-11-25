import { type EditBaseline, readRefusal, type runnerApi, type SaveOutcome, settingValueText } from 'fleet';

import type { HostSubscription } from '../core/host-subscription.domain';
import type { HostWorkspace } from '../core/host-workspace.domain';
import {
  type HostRecord,
  RECORD_FIELDS,
  type HostRecordKind,
  type RecordDraft,
  type RecordField,
  recordFields,
} from './host-records.domain';

/** A workspace read as the record its panel holds. */
export function workspaceRecord(workspace: HostWorkspace): HostRecord {
  const { revision, state, settings } = workspace;
  return { kind: 'workspace', key: workspace.workspace_id, revision, state, settings };
}

/** A subscription read as the record its panel holds. */
export function subscriptionRecord(subscription: HostSubscription): HostRecord {
  const { revision, state, settings } = subscription;
  return { kind: 'subscription', key: subscription.slug, revision, state, settings };
}

function isRepo(value: unknown): value is { name: unknown; url: unknown } {
  return typeof value === 'object' && value !== null && 'name' in value && 'url' in value;
}

/** A stated value as `field`'s text: a list joined by commas, repositories one `name url` per line, anything else as
 * a string; an unstated key is blank. */
function fieldText(field: RecordField, value: unknown): string {
  if (value === undefined || value === null) return '';
  if (field.type === 'list' && Array.isArray(value)) return value.map(String).join(', ');
  if (field.type === 'repos' && Array.isArray(value)) {
    return value.map((repo) => (isRepo(repo) ? `${String(repo.name)} ${String(repo.url)}` : settingValueText(repo))).join('\n');
  }
  return typeof value === 'string' ? value : settingValueText(value);
}

/** What an edit of `record` starts from: its key, the revision it was read at, and each editable key's stated value
 * as its field shows it, all from one read. `null` for a record on a host keeping no records, which takes no edit. */
export function toRecordBaseline(record: HostRecord): EditBaseline<RecordDraft> | null {
  if (record.revision === null) return null;
  const draft = Object.fromEntries(recordFields(record.kind, 'edit').map((f) => [f.name, fieldText(f, record.settings[f.name])]));
  return { id: record.key, revision: record.revision, draft };
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

function parseRepos(text: string): { value: runnerApi.HostWorkspaceRepoWrite[] } | { error: string } {
  const repos: runnerApi.HostWorkspaceRepoWrite[] = [];
  for (const line of text.split('\n').map((l) => l.trim()).filter((l) => l !== '')) {
    const parts = line.split(/\s+/);
    if (parts.length !== 2) return { error: 'Write each repository as its name and its URL.' };
    repos.push({ name: parts[0], url: parts[1] });
  }
  return { value: repos };
}

/** Why `text` cannot stand in `field` on a form of `mode`, or `null` while it can. */
export function recordFieldError(field: RecordField, mode: 'create' | 'edit', text: string): string | null {
  const parsed = parseRecordField(field, text);
  if ('error' in parsed) return parsed.error;
  if (parsed.value === null && (mode === 'create' ? field.required : field.refusesDefault)) return 'Required.';
  return null;
}

/** A draft read as its request body, or each field's reason it reads as none. A create states every non-blank field;
 * an edit only the fields changed from `before`, a field cleared to blank returning its key to its default. */
export function toRecordBody(
  kind: HostRecordKind,
  before: RecordDraft | null,
  after: RecordDraft,
): { body: Record<string, unknown> } | { errors: Partial<Record<string, string>> } {
  const mode = before === null ? 'create' : 'edit';
  const body: Record<string, unknown> = {};
  const errors: Record<string, string> = {};
  for (const f of recordFields(kind, mode)) {
    const text = after[f.name] ?? '';
    if (before !== null && text.trim() === (before[f.name] ?? '').trim()) continue;
    const error = recordFieldError(f, mode, text);
    if (error !== null) {
      errors[f.name] = error;
      continue;
    }
    const parsed = parseRecordField(f, text) as { value: unknown };
    if (parsed.value !== null || mode === 'edit') body[f.name] = parsed.value;
  }
  return Object.keys(errors).length > 0 ? { errors } : { body };
}

function isSettingsProblem(error: unknown): error is runnerApi.SettingsProblem {
  return typeof error === 'object' && error !== null && typeof (error as { code?: unknown }).code === 'string';
}

/** A refused write of a `kind` record as a {@link SaveOutcome}: a revision conflict on the form line, keeping the
 * draft; each refusal naming a field the form states beside it, any other on the form line. Anything else reads
 * through `readRefusal`. */
export function recordRefusal(error: unknown, kind: HostRecordKind): SaveOutcome<RecordDraft> {
  const names = new Set(RECORD_FIELDS[kind].map((f) => f.name));
  const fieldOf = (key: string | null | undefined): string | null => (key && names.has(key) ? key : null);
  if (!isSettingsProblem(error)) return readRefusal<RecordDraft>(error, { field: fieldOf, kinds: {} });
  if (error.code === 'revision_conflict') return { ok: false, fields: {}, form: error.detail };
  const errors = error.errors ?? [{ key: error.key ?? '', message: error.detail }];
  const fields: Record<string, string> = {};
  const form: string[] = [];
  for (const e of errors) {
    const name = fieldOf(e.key);
    if (name === null) form.push(e.key ? `${e.key}: ${e.message}` : e.message);
    else fields[name] = fields[name] ? `${fields[name]}; ${e.message}` : e.message;
  }
  return { ok: false, fields, form: form.length > 0 ? form.join('; ') : null };
}
