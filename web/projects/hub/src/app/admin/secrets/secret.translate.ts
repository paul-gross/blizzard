// bzh:frontend-domain-boundary — the one place the secret wire is read and written.
import { type EditBaseline, type RefusalMap } from 'fleet/forms';
import { type SecretCreateRequestWritable, type SecretView } from 'fleet/hub-api';

import type { ConfigRecordRef } from '../config-history.domain';
import type { Secret, SecretCreateDraft, SecretReplaceDraft } from './secret.domain';
import { EMPTY_SECRET_REPLACE_DRAFT } from './secret.domain';

/** A served secret as the domain holds it. */
export function fromSecretView(wire: SecretView): Secret {
  return {
    name: wire.name,
    revision: wire.revision,
    retired: wire.retired,
    retirable: wire.retirable,
    replacedBy: wire.replaced_by,
    replacedByName: wire.replaced_by_name,
    references: wire.references.map((ref) => ({ kind: ref.kind, key: ref.key, label: ref.label })),
  };
}

/** The record kind the change history and the CLI change verb name a secret by. */
export const SECRET_RECORD_KIND = 'secret';

/** The links a secret's referring records are shown as. */
export function secretReferenceRefs(secret: Secret): readonly ConfigRecordRef[] {
  return secret.references.map((ref) => ({ kind: ref.kind, key: ref.key, label: ref.label }));
}

/** What a value replacement starts from: the secret's name, the revision it was read at, and a blank draft. */
export function toReplaceBaseline(secret: Secret): EditBaseline<SecretReplaceDraft> {
  return { id: secret.name, revision: secret.revision, draft: { ...EMPTY_SECRET_REPLACE_DRAFT } };
}

/** The create body a draft states: the name trimmed, the value exactly as typed (a pasted PEM keeps its line breaks). */
export function toCreateRequest(draft: SecretCreateDraft): SecretCreateRequestWritable {
  return { name: draft.name.trim(), value: draft.value };
}

/** How the hub's secret refusals reach the drafts: a body field to the field of the same name, and a taken name to
 * the name. A revision conflict falls to the form line. */
export const SECRET_REFUSALS: RefusalMap<SecretCreateDraft & SecretReplaceDraft> = {
  field: (path) => (path === 'name' || path === 'value' ? path : null),
  kinds: { 'urn:blizzard:problem:secret-name-taken': 'name' },
};
