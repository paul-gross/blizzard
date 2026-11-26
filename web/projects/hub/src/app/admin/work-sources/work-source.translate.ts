// bzh:frontend-domain-boundary — the one place the work-source wire is read and written.
import {
  type EditBaseline,
  RecordKind,
  type RefusalMap,
  type WorkSourceCreateRequest,
  type WorkSourcePatchRequest,
  type WorkSourceSummary,
} from 'fleet';

import type { ConfigRecordRef } from '../config-history.domain';
import type { WorkSource, WorkSourceDraft } from './work-source.domain';

/** A served work source as the domain holds it. */
export function fromWorkSourceSummary(wire: WorkSourceSummary): WorkSource {
  return {
    id: wire.work_source_id,
    revision: wire.revision,
    slug: wire.slug,
    name: wire.name,
    provider: wire.provider,
    locator: wire.locator,
    forgeId: wire.forge_id,
    forgeSlug: wire.forge_slug,
    forgeName: wire.forge_name,
    credentialId: wire.credential_id,
    credentialSlug: wire.credential_slug,
    annotate: wire.annotate,
    edit: wire.edit,
    builtIn: wire.built_in,
    retired: wire.retired,
    createdBy: wire.created_by,
    createdByName: wire.created_by_name,
  };
}

/** The record kind the change history and the CLI change verb name a work source by. */
export const WORK_SOURCE_RECORD_KIND = 'work_source';

/** The link a work source's forge is shown as; only called for a source that names one. */
export function workSourceForgeRef(source: WorkSource): ConfigRecordRef {
  return { kind: RecordKind.FORGE, key: source.forgeId!, label: source.forgeSlug };
}

/** The form's opening draft for `source`: the forge and credential as ids, a missing value as blank. */
export function toDraft(source: WorkSource): WorkSourceDraft {
  return {
    slug: source.slug ?? '',
    name: source.name,
    provider: source.provider ?? '',
    locator: source.locator ?? '',
    forge: source.forgeId ?? '',
    credential: source.credentialId ?? '',
    annotate: source.annotate,
  };
}

/** What an edit of `source` starts from: its id, the revision it was read at, and its draft, all from that one read.
 * `null` for a source that cannot be edited — the built-in one, which has no id or revision. */
export function toEditBaseline(source: WorkSource): EditBaseline<WorkSourceDraft> | null {
  if (source.id === null || source.revision === null) return null;
  return { id: source.id, revision: source.revision, draft: toDraft(source) };
}

const TEXT_FIELDS = ['slug', 'name', 'provider', 'locator'] as const;
const CHOICE_FIELDS = ['forge', 'credential'] as const;

/** The create body a draft states: text trimmed, a blank name or credential left out (the name then takes the slug),
 * and `annotate` always sent. */
export function toCreateRequest(draft: WorkSourceDraft): WorkSourceCreateRequest {
  const name = draft.name.trim();
  const credential = draft.credential.trim();
  return {
    slug: draft.slug.trim(),
    provider: draft.provider.trim(),
    locator: draft.locator.trim(),
    ...(name ? { name } : {}),
    forge: draft.forge.trim(),
    ...(credential ? { credential } : {}),
    annotate: draft.annotate,
  };
}

/** The edit body for `after` against the draft it started from: only fields whose trimmed value changed. A cleared
 * text field goes as `null`; a cleared choice goes as blank. */
export function toEditRequest(before: WorkSourceDraft, after: WorkSourceDraft): WorkSourcePatchRequest {
  const body: WorkSourcePatchRequest = {};
  for (const key of TEXT_FIELDS) {
    const next = after[key].trim();
    if (next !== before[key].trim()) body[key] = next === '' ? null : next;
  }
  for (const key of CHOICE_FIELDS) {
    const next = after[key].trim();
    if (next !== before[key].trim()) body[key] = next;
  }
  if (after.annotate !== before.annotate) body.annotate = after.annotate;
  return body;
}

const REFUSAL_FIELDS: Readonly<Record<string, keyof WorkSourceDraft>> = {
  slug: 'slug',
  name: 'name',
  provider: 'provider',
  locator: 'locator',
  forge: 'forge',
  credential: 'credential',
  annotate: 'annotate',
};

/** How the hub's work-source refusals reach the draft: a body field to the field of the same name (the deprecated
 * `secret` to none), and a taken slug or locator to its field. A revision conflict falls to the form line. */
export const WORK_SOURCE_REFUSALS: RefusalMap<WorkSourceDraft> = {
  field: (path) => (Object.hasOwn(REFUSAL_FIELDS, path) ? REFUSAL_FIELDS[path] : null),
  kinds: {
    'urn:blizzard:problem:work-source-slug-taken': 'slug',
    'urn:blizzard:problem:work-source-locator-taken': 'locator',
  },
};

