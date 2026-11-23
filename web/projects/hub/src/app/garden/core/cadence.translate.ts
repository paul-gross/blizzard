// bzh:frontend-domain-boundary — the one place the cadence wire is read and written; an unforeseen vocabulary value
// becomes an `Unlisted` here and is resent as itself (bzh:frontend-open-vocabulary).
import {
  AttentionReason,
  CadenceAnchor as WireCadenceAnchor,
  type CadenceAttentionView,
  type CadenceErrandView,
  type CadenceCreateRequest,
  type CadenceEditRequest,
  type CadenceView,
  type ChoreDocument,
  type ChoreView,
  type EditBaseline,
  ErrandState as WireErrandState,
  openLookup,
  type RefusalMap,
  RunMode,
} from 'fleet';

import {
  type Cadence,
  type CadenceAnchor,
  type CadenceAttention,
  type CadenceAttentionReason,
  type CadenceDraft,
  type CadenceErrand,
  type Chore,
  type ChoreDraft,
  type ChoreMode,
  type ErrandState,
} from './cadence.domain';
import { type Vocabulary, vocabularyText } from './vocabulary.domain';

const unlisted = (raw: string) => ({ unlisted: raw });

const anchorOf = openLookup<WireCadenceAnchor, Vocabulary<CadenceAnchor>>(
  {
    [WireCadenceAnchor.SCHEDULED]: 'scheduled',
    [WireCadenceAnchor.STARTED]: 'started',
    [WireCadenceAnchor.FINISHED]: 'finished',
  },
  unlisted,
);

const modeOf = openLookup<RunMode, Vocabulary<ChoreMode>>(
  { [RunMode.FULL]: 'full', [RunMode.DELTA]: 'delta' },
  unlisted,
);

const reasonOf = openLookup<AttentionReason, Vocabulary<CadenceAttentionReason>>(
  {
    [AttentionReason.ROUTINE_UNKNOWN]: 'routine_unknown',
    [AttentionReason.ROUTINE_RETIRED]: 'routine_retired',
    [AttentionReason.GRAPH_UNRESOLVED]: 'graph_unresolved',
    [AttentionReason.SCOPE_UNKNOWN]: 'scope_unknown',
    [AttentionReason.SCOPE_RETIRED]: 'scope_retired',
    [AttentionReason.SCOPE_UNLINKED]: 'scope_unlinked',
    [AttentionReason.NO_SCOPES]: 'no_scopes',
    [AttentionReason.NO_CHORES]: 'no_chores',
  },
  unlisted,
);

const errandStateOf = openLookup<WireErrandState, Vocabulary<ErrandState>>(
  {
    [WireErrandState.DUE]: 'due',
    [WireErrandState.RUNNING]: 'running',
    [WireErrandState.WAITING]: 'waiting',
    [WireErrandState.STOPPED]: 'stopped',
  },
  unlisted,
);

function choreOf(wire: ChoreView): Chore {
  return {
    id: wire.chore_id,
    key: wire.key,
    routine: wire.routine,
    scopes: wire.scopes,
    mode: modeOf(wire.mode),
    promote: wire.promote,
    note: wire.note,
    urgency: wire.urgency,
    scopesSetAt: wire.scopes_set_at,
  };
}

function attentionOf(wire: CadenceAttentionView): CadenceAttention {
  return { chore: wire.chore, scope: wire.scope, reason: reasonOf(wire.reason) };
}

/** A served cadence as the domain holds it. */
export function fromCadenceView(wire: CadenceView): Cadence {
  return {
    id: wire.cadence_id,
    revision: wire.revision,
    slug: wire.slug,
    name: wire.name,
    projectId: wire.project_id,
    projectName: wire.project_name,
    projectSlug: wire.project_slug,
    schedule: wire.schedule === null ? null : { cron: wire.schedule.cron, zone: wire.schedule.zone },
    spacingMinutes: wire.spacing_minutes,
    anchor: wire.anchor === null ? null : anchorOf(wire.anchor),
    chores: wire.chores.map(choreOf),
    attention: wire.attention.map(attentionOf),
    errandCounts: { ...wire.errand_counts },
    nextMoments: [...wire.next_moments],
    retired: wire.retired,
    createdAt: wire.created_at,
  };
}

/** A served errand as the domain holds it. */
export function fromCadenceErrandView(wire: CadenceErrandView): CadenceErrand {
  return {
    chore: wire.chore,
    scope: wire.scope,
    state: errandStateOf(wire.state),
    attention: wire.attention.map(attentionOf),
    lastRun: wire.last_run === null ? null : { chunkId: wire.last_run.chunk_id, scheduledAt: wire.last_run.scheduled_at },
    dueAt: wire.due_at,
  };
}

/** A spacing's minutes as the text the hub reads: the largest whole unit of `d`, `h`, or `m`; blank for none. */
export function spacingDraftText(minutes: number | null): string {
  if (minutes === null) return '';
  if (minutes % 1440 === 0) return `${minutes / 1440}d`;
  if (minutes % 60 === 0) return `${minutes / 60}h`;
  return `${minutes}m`;
}

/** The form's opening draft for `cadence`. An anchor this build does not know opens unstated; an edit leaving it
 * untouched never sends it, so the stored value stays. */
export function toDraft(cadence: Cadence): CadenceDraft {
  return {
    slug: cadence.slug,
    name: cadence.name,
    cron: cadence.schedule?.cron ?? '',
    zone: cadence.schedule?.zone ?? '',
    spacing: spacingDraftText(cadence.spacingMinutes),
    anchor: cadence.anchor === null || typeof cadence.anchor !== 'string' ? 'unstated' : cadence.anchor,
  };
}

/** What an edit of `cadence` starts from: its id, the revision it was read at, and its draft, all from that one read. */
export function toEditBaseline(cadence: Cadence): EditBaseline<CadenceDraft> {
  return { id: cadence.id, revision: cadence.revision, draft: toDraft(cadence) };
}

const REFUSAL_FIELDS: Readonly<Record<string, keyof CadenceDraft>> = {
  slug: 'slug',
  name: 'name',
  'schedule.cron': 'cron',
  'schedule.zone': 'zone',
  schedule: 'cron',
  spacing: 'spacing',
  anchor: 'anchor',
};

/** How the hub's cadence refusals reach the draft: a field path to its field, and a slug already taken to the slug. */
export const CADENCE_REFUSALS: RefusalMap<CadenceDraft> = {
  field: (path) => (Object.hasOwn(REFUSAL_FIELDS, path) ? REFUSAL_FIELDS[path] : null),
  kinds: { 'urn:blizzard:problem:cadence-slug-taken': 'slug' },
};

function scheduleOf(draft: CadenceDraft): { cron: string | null; zone: string | null } | null {
  const cron = draft.cron.trim();
  const zone = draft.zone.trim();
  return cron || zone ? { cron: cron || null, zone: zone || null } : null;
}

/** The create body a draft states: a field left blank is left out, so the hub's own default applies. */
export function toCreateRequest(draft: CadenceDraft): CadenceCreateRequest {
  const schedule = scheduleOf(draft);
  const name = draft.name.trim();
  const spacing = draft.spacing.trim();
  return {
    slug: draft.slug.trim(),
    ...(name ? { name } : {}),
    ...(schedule ? { schedule } : {}),
    ...(spacing ? { spacing } : {}),
    ...(draft.anchor !== 'unstated' ? { anchor: draft.anchor } : {}),
  };
}

/** The edit body for `after` against the draft it started from: only the fields that changed. The schedule goes as a
 * pair when its cron or zone changed, `null` once both are blank; a blank spacing is `null`; `unstated` is anchor
 * `null`. */
export function toEditRequest(before: CadenceDraft, after: CadenceDraft): CadenceEditRequest {
  const name = after.name.trim();
  const spacing = after.spacing.trim();
  const scheduleChanged =
    before.cron.trim() !== after.cron.trim() || before.zone.trim() !== after.zone.trim();
  return {
    ...(name !== before.name.trim() ? { name } : {}),
    ...(scheduleChanged ? { schedule: scheduleOf(after) } : {}),
    ...(spacing !== before.spacing.trim() ? { spacing: spacing || null } : {}),
    ...(after.anchor !== before.anchor ? { anchor: after.anchor === 'unstated' ? null : after.anchor } : {}),
  };
}

/** A held chore as the document an edit sends back — every field resent as read, an unlisted mode included. */
export function choreDocument(chore: Chore): ChoreDocument {
  return {
    key: chore.key,
    routine: chore.routine,
    scopes: chore.scopes === null || chore.scopes === 'all' ? chore.scopes : [...chore.scopes],
    mode: vocabularyText(chore.mode) as RunMode,
    promote: chore.promote,
    note: chore.note,
    urgency: chore.urgency,
  };
}

/** The chore form's opening draft for a held chore. */
export function toChoreDraft(chore: Chore): ChoreDraft {
  return {
    key: chore.key,
    routine: chore.routine,
    scopes: chore.scopes === 'all' ? 'all' : (chore.scopes ?? []).join(', '),
    mode: vocabularyText(chore.mode),
    promote: chore.promote,
    note: chore.note ?? '',
    urgency: chore.urgency ?? '',
  };
}

/** The document a chore draft states. Scopes read `all`, a comma- or space-separated slug list, or blank for the
 * routine's default scope. */
export function choreDraftDocument(draft: ChoreDraft): ChoreDocument {
  const scopes = draft.scopes.trim();
  return {
    key: draft.key.trim(),
    routine: draft.routine.trim(),
    scopes: scopes === 'all' ? 'all' : scopes === '' ? null : scopes.split(/[\s,]+/).filter((s) => s !== ''),
    mode: draft.mode as RunMode,
    promote: draft.promote,
    note: draft.note.trim() || null,
    urgency: draft.urgency || null,
  };
}

/** What a chore add, edit, or remove starts from: the cadence's id, the revision it was read at, and its chores, all
 * from that one read. */
export function toChoresBaseline(cadence: Cadence): EditBaseline<readonly Chore[]> {
  return { id: cadence.id, revision: cadence.revision, draft: [...cadence.chores] };
}

/**
 * How the hub's refusals reach the chore at `index` of the chore list sent: its own fields (and its scope entries) to
 * the draft field, and anything else — another chore, the list, a conflict — to the form line.
 */
export function choreRefusals(index: number): RefusalMap<ChoreDraft> {
  const prefix = `chores[${index}].`;
  return {
    field: (path) => {
      if (!path.startsWith(prefix)) return null;
      const field = path.slice(prefix.length).replace(/\[\d+\]$/, '');
      return Object.hasOwn(CHORE_REFUSAL_FIELDS, field) ? CHORE_REFUSAL_FIELDS[field] : null;
    },
    kinds: {},
  };
}

const CHORE_REFUSAL_FIELDS: Readonly<Record<string, keyof ChoreDraft>> = {
  key: 'key',
  routine: 'routine',
  scopes: 'scopes',
  mode: 'mode',
  promote: 'promote',
  note: 'note',
  urgency: 'urgency',
};

/** The whole chore list with `draft` added at its end. */
export function choresAdding(chores: readonly Chore[], draft: ChoreDraft): ChoreDocument[] {
  return [...chores.map(choreDocument), choreDraftDocument(draft)];
}

/** The whole chore list with the chore keyed `key` replaced, in place, by `draft`. */
export function choresReplacing(chores: readonly Chore[], key: string, draft: ChoreDraft): ChoreDocument[] {
  return chores.map((chore) => (chore.key === key ? { ...choreDraftDocument(draft), key } : choreDocument(chore)));
}

/** The whole chore list without the chore keyed `key`. */
export function choresRemoving(chores: readonly Chore[], key: string): ChoreDocument[] {
  return chores.filter((chore) => chore.key !== key).map(choreDocument);
}
