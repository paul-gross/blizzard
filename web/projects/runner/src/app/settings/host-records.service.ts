import type { EditBaseline, SaveOutcome } from 'fleet/forms';

import type { HostRecordKind, RecordDraft } from './host-records.domain';
import {
  injectRecordCreateMutation,
  injectRecordEditMutation,
  injectRecordEnableMutation,
  injectRecordRetireMutation,
} from './host-records.mutations';
import { recordRefusal, toRecordCreate, toRecordEdit } from './host-records.translate';

/** A verb on a host record, each with its own control and its own pending state. */
export type RecordVerb = 'create' | 'edit' | 'retire' | 'enable';

/** Writes host records of one kind: domain in, a `SaveOutcome` out. */
export interface HostRecordsService {
  /** Creates a record stating every non-blank field of `draft`. */
  create(draft: RecordDraft): Promise<SaveOutcome<RecordDraft>>;
  /** Edits the baseline's record with the fields `after` changed, against the baseline's revision as `If-Match`. */
  edit(before: EditBaseline<RecordDraft>, after: RecordDraft): Promise<SaveOutcome<RecordDraft>>;
  retire(key: string): Promise<SaveOutcome<RecordDraft>>;
  enable(key: string): Promise<SaveOutcome<RecordDraft>>;
  /** Whether `verb` is in flight. */
  pending(verb: RecordVerb): boolean;
}

/** The host-records service over `kind`'s four mutations. A write resolves once its invalidations settle; a refusal
 * is answered, never thrown — a revision conflict on the form line, never retried against a newer revision. */
export function injectHostRecordsService(kind: () => HostRecordKind): HostRecordsService {
  const mutations = {
    create: injectRecordCreateMutation(kind),
    edit: injectRecordEditMutation(kind),
    retire: injectRecordRetireMutation(kind),
    enable: injectRecordEnableMutation(kind),
  };
  const send = async (write: () => Promise<void>): Promise<SaveOutcome<RecordDraft>> => {
    try {
      await write();
      return { ok: true };
    } catch (error) {
      return recordRefusal(error, kind());
    }
  };
  return {
    create(draft) {
      const read = toRecordCreate(kind(), draft);
      if ('errors' in read) return Promise.resolve({ ok: false, fields: read.errors, form: null });
      return send(() => mutations.create.mutateAsync(read.write));
    },
    edit(before, after) {
      const read = toRecordEdit(kind(), before.draft, after);
      if ('errors' in read) return Promise.resolve({ ok: false, fields: read.errors, form: null });
      return send(() => mutations.edit.mutateAsync({ ...read.write, key: before.id, revision: before.revision }));
    },
    retire: (key) => send(() => mutations.retire.mutateAsync({ kind: kind(), key })),
    enable: (key) => send(() => mutations.enable.mutateAsync({ kind: kind(), key })),
    pending: (verb) => {
      const pending: Readonly<Record<RecordVerb, () => boolean>> = {
        create: () => mutations.create.isPending(),
        edit: () => mutations.edit.isPending(),
        retire: () => mutations.retire.isPending(),
        enable: () => mutations.enable.isPending(),
      };
      return pending[verb]();
    },
  };
}
