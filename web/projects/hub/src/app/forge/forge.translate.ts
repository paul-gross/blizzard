// bzh:frontend-domain-boundary — the one place the forge wire is read and written.
import type { EditBaseline, ForgeCreateRequest, ForgeDetail, ForgePatchRequest, ForgeSummary, RefusalMap } from 'fleet';

import type { Forge, ForgeDraft, ForgeRecord } from './forge.domain';

/** A served forge summary as the domain holds it. */
export function fromForgeSummary(wire: ForgeSummary): Forge {
  return {
    id: wire.forge_id,
    slug: wire.slug,
    name: wire.name,
    kind: wire.kind,
    apiBase: wire.api_base,
    webBase: wire.web_base,
    webOrigin: wire.web_origin,
    revision: wire.revision,
    retired: wire.retired,
  };
}

/** A served forge detail as the domain holds it. */
export function fromForgeDetail(wire: ForgeDetail): ForgeRecord {
  return {
    ...fromForgeSummary(wire),
    createdBy: wire.created_by,
    createdByName: wire.created_by_name,
    offered: [...wire.offered],
    budgets: wire.budgets.map((budget) => ({
      bucket: budget.bucket,
      secretName: budget.secret_name,
      remaining: budget.remaining,
      limit: budget.limit,
      reset: budget.reset,
    })),
    repositories: wire.repositories.map((ref) => ({ key: ref.key, label: ref.label })),
    workSources: wire.work_sources.map((ref) => ({ key: ref.key, label: ref.label })),
  };
}

/** The form's opening draft for `forge`. */
export function toForgeDraft(forge: Forge): ForgeDraft {
  return { slug: forge.slug, name: forge.name, kind: forge.kind, apiBase: forge.apiBase, webBase: forge.webBase ?? '' };
}

/** What an edit of `forge` starts from: its id, the revision it was read at, and its draft, all from that one read. */
export function toForgeEditBaseline(forge: Forge): EditBaseline<ForgeDraft> {
  return { id: forge.id, revision: forge.revision, draft: toForgeDraft(forge) };
}

const REFUSAL_FIELDS: Readonly<Record<string, keyof ForgeDraft>> = {
  slug: 'slug',
  name: 'name',
  kind: 'kind',
  api_base: 'apiBase',
  web_base: 'webBase',
};

/** How the hub's forge refusals reach the draft: a field path to its field, a slug taken to the slug, and an origin
 * taken to the API base. A revision conflict is not mapped, so it reaches the form line with its message. */
export const FORGE_REFUSALS: RefusalMap<ForgeDraft> = {
  field: (path) => (Object.hasOwn(REFUSAL_FIELDS, path) ? REFUSAL_FIELDS[path] : null),
  kinds: {
    'urn:blizzard:problem:forge-slug-taken': 'slug',
    'urn:blizzard:problem:forge-origin-taken': 'apiBase',
  },
};

/** The create body a draft states: every field trimmed, a blank name or web base left out. */
export function toForgeCreateRequest(draft: ForgeDraft): ForgeCreateRequest {
  const name = draft.name.trim();
  const webBase = draft.webBase.trim();
  return {
    slug: draft.slug.trim(),
    kind: draft.kind.trim(),
    api_base: draft.apiBase.trim(),
    ...(name ? { name } : {}),
    ...(webBase ? { web_base: webBase } : {}),
  };
}

/** The edit body for `after` against the draft it started from: only the trimmed fields that changed, a blank one as
 * `null`. The kind is fixed at create and never sent. */
export function toForgeEditRequest(before: ForgeDraft, after: ForgeDraft): ForgePatchRequest {
  const changed = (key: 'slug' | 'name' | 'apiBase' | 'webBase'): string | null | undefined => {
    const next = after[key].trim();
    return next === before[key].trim() ? undefined : next || null;
  };
  const slug = changed('slug');
  const name = changed('name');
  const apiBase = changed('apiBase');
  const webBase = changed('webBase');
  return {
    ...(slug !== undefined ? { slug } : {}),
    ...(name !== undefined ? { name } : {}),
    ...(apiBase !== undefined ? { api_base: apiBase } : {}),
    ...(webBase !== undefined ? { web_base: webBase } : {}),
  };
}
