import { settingValueText, readRefusal } from 'fleet/core';
import { type EditBaseline, type SaveOutcome } from 'fleet/forms';
import { type runnerApi } from 'fleet/runner-api';

import type { HostSubscription } from '../core/host-subscription.domain';
import type { HostWorkspace } from '../core/host-workspace.domain';
import {
  type HostRecord,
  RECORD_FIELDS,
  type HostRecordKind,
  type RecordDraft,
  type RecordField,
  type RecordFieldType,
  type RecordFieldValue,
  recordFieldError,
  recordFields,
  parseRecordField,
} from './host-records.domain';
import { settingsProblemOutcome } from './settings.translate';

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

type Fields<K extends HostRecordKind> = (typeof RECORD_FIELDS)[K][number];

/** Each field's name to the value its text reads as. */
type Shape<Fs extends { readonly name: string; readonly type: RecordFieldType }> = {
  [F in Fs as F['name']]: RecordFieldValue<F['type']>;
};

/** `Request`, while `S` names exactly its keys, each with a value the request takes; `never` once the field table and
 * the generated request drift, so every body built against it stops compiling. */
type Keyed<S, Request> = [keyof S] extends [keyof Request]
  ? [keyof Request] extends [keyof S]
    ? { [N in keyof S]: N extends keyof Request ? (S[N] extends NonNullable<Request[N]> ? true : false) : false }[keyof S] extends true
      ? Request
      : never
    : never
  : never;

type CreateBody<K extends HostRecordKind, Request> = Keyed<Shape<Fields<K>>, Request>;
type EditBody<K extends HostRecordKind, Request> = Keyed<Shape<Exclude<Fields<K>, { readonly immutable: true }>>, Request>;

/** A create of a `kind` record, its body its create request. */
export type RecordCreate =
  | { readonly kind: 'workspace'; readonly body: CreateBody<'workspace', runnerApi.HostWorkspaceCreateRequest> }
  | { readonly kind: 'subscription'; readonly body: CreateBody<'subscription', runnerApi.HostSubscriptionCreateRequest> };

/** A sparse edit of a `kind` record, its body its patch request. */
export type RecordEdit =
  | { readonly kind: 'workspace'; readonly body: EditBody<'workspace', runnerApi.HostWorkspacePatchRequest> }
  | { readonly kind: 'subscription'; readonly body: EditBody<'subscription', runnerApi.HostSubscriptionPatchRequest> };

type BodyRead<W> = { readonly write: W } | { readonly errors: Partial<Record<string, string>> };

function readBody(kind: HostRecordKind, before: RecordDraft | null, after: RecordDraft): BodyRead<Record<string, unknown>> {
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
  return Object.keys(errors).length > 0 ? { errors } : { write: body };
}

/** A create's draft read as its request, stating every non-blank field, or each field's reason it reads as none.
 * The body holds only {@link RECORD_FIELDS} keys, which {@link RecordCreate} holds to the request's. */
export function toRecordCreate(kind: HostRecordKind, draft: RecordDraft): BodyRead<RecordCreate> {
  const read = readBody(kind, null, draft);
  return 'errors' in read ? read : { write: { kind, body: read.write } as RecordCreate };
}

/** An edit's draft read as its request, stating only the fields changed from `before` — a field cleared to blank
 * returning its key to its default — or each field's reason it reads as none. */
export function toRecordEdit(kind: HostRecordKind, before: RecordDraft, after: RecordDraft): BodyRead<RecordEdit> {
  const read = readBody(kind, before, after);
  return 'errors' in read ? read : { write: { kind, body: read.write } as RecordEdit };
}

/** A refused write of a `kind` record as a {@link SaveOutcome}, read by {@link settingsProblemOutcome} with each
 * refusal naming a field the form states beside it. Anything else reads through `readRefusal`. */
export function recordRefusal(error: unknown, kind: HostRecordKind): SaveOutcome<RecordDraft> {
  const names = new Set<string>(RECORD_FIELDS[kind].map((f) => f.name));
  const fieldOf = (key: string | null | undefined): string | null => (key && names.has(key) ? key : null);
  return settingsProblemOutcome<RecordDraft>(error, fieldOf) ?? readRefusal<RecordDraft>(error, { field: fieldOf, kinds: {} });
}
