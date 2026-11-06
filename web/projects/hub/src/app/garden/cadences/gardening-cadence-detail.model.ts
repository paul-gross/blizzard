import {
  type CadenceAttentionView,
  type CadenceEditRequest,
  type CadenceErrandView,
  type CadenceView,
  type ChoreDocument,
  type ChoreView,
  formatAbsolute,
} from 'fleet';

import type { CadenceDraft } from './cadence-form';
import type { CadenceLifecycleVars } from './cadence-lifecycle.mutations';
import type { AttentionVm, CadencePanelVm, ChorePanelVm, ErrandRowVm } from './cadence-panel';
import { attentionReasonLabel, choreModeLabel, errandStateLook } from './cadence-vocabulary';
import type { ChoreDraft } from './chore-form';
import { rhythmText } from './gardening-cadences-page.model';

/** The cadence `slug` names, or `null` on the bare child route or for a slug no loaded cadence holds. */
export function cadenceBySlug(slug: string | null, cadences: readonly CadenceView[]): CadenceView | null {
  return slug === null ? null : (cadences.find((c) => c.slug === slug) ?? null);
}

/** `cadence`'s `retired` flag as it reads once a pending retire or enable of it settles — `null` while none is. */
export function cadenceOverrideRetired(
  cadence: CadenceView | null,
  pending: readonly CadenceLifecycleVars[],
): boolean | null {
  if (cadence === null) return null;
  const vars = pending.find((v) => v.cadenceId === cadence.cadence_id);
  return vars ? vars.retired : null;
}

/** One attention entry as the panel lists it: what it stops, and why. */
export function attentionVm(attention: CadenceAttentionView): AttentionVm {
  return { chore: attention.chore, scope: attention.scope, reason: attentionReasonLabel(attention.reason) };
}

/** Which scopes a chore runs over, as text: every linked scope, the listed ones, or the routine's default. */
export function choreScopesText(scopes: ChoreView['scopes']): string {
  if (scopes === 'all') return 'all scopes';
  if (scopes === null || scopes.length === 0) return "the routine's default scope";
  return scopes.join(', ');
}

/** One errand as a chore's table row: its scope, state, last run, when it is next due, and why it is stopped. */
export function errandRow(errand: CadenceErrandView): ErrandRowVm {
  return {
    scope: errand.scope,
    ...errandStateLook(errand.state),
    lastRunChunkId: errand.last_run?.chunk_id ?? null,
    lastRunAt: errand.last_run?.scheduled_at ?? null,
    dueAt: errand.due_at,
    attention: errand.attention.map((a) => attentionReasonLabel(a.reason)).join('; '),
  };
}

/** The cadence's chores, each with its own errands — `null` errands while the errands read is pending. */
export function choreRows(cadence: CadenceView, errands: readonly CadenceErrandView[] | undefined): readonly ChorePanelVm[] {
  return cadence.chores.map((chore) => ({
    key: chore.key,
    routine: chore.routine,
    scopes: choreScopesText(chore.scopes),
    mode: choreModeLabel(chore.mode),
    promote: chore.promote,
    note: chore.note,
    draft: choreDraftOf(chore),
    errands: errands === undefined ? null : errands.filter((e) => e.chore === chore.key).map(errandRow),
  }));
}

/** The cadence panel's view model — `null` with no cadence selected. */
export function cadencePanelVm(
  cadence: CadenceView | null,
  overrideRetired: boolean | null,
  errands: readonly CadenceErrandView[] | undefined,
): CadencePanelVm | null {
  if (cadence === null) return null;
  return {
    cadenceId: cadence.cadence_id,
    slug: cadence.slug,
    name: cadence.name,
    revision: cadence.revision,
    retired: cadence.retired,
    renderedRetired: overrideRetired ?? cadence.retired,
    rhythm: rhythmText(cadence),
    nextMoments: cadence.next_moments.map((iso) => ({ iso, text: formatAbsolute(iso) })),
    attention: cadence.attention.map(attentionVm),
    chores: choreRows(cadence, errands),
    draft: cadenceDraftOf(cadence),
  };
}

/** A spacing's minutes as the text the hub reads: the largest whole unit of `d`, `h`, or `m`. */
export function spacingInput(minutes: number | null): string {
  if (minutes === null) return '';
  if (minutes % 1440 === 0) return `${minutes / 1440}d`;
  if (minutes % 60 === 0) return `${minutes / 60}h`;
  return `${minutes}m`;
}

/** The edit form's opening draft: the cadence's own name and rhythm. */
export function cadenceDraftOf(cadence: CadenceView): CadenceDraft {
  return {
    slug: cadence.slug,
    name: cadence.name,
    cron: cadence.schedule?.cron ?? '',
    zone: cadence.schedule?.zone ?? '',
    spacing: spacingInput(cadence.spacing_minutes),
    anchor: cadence.anchor ?? '',
  };
}

/** The edit body a draft states: its name and whole rhythm, a blank schedule, spacing, or anchor clearing it. */
export function editCadenceRequest(draft: CadenceDraft): CadenceEditRequest {
  const cron = draft.cron.trim();
  const zone = draft.zone.trim();
  return {
    name: draft.name.trim(),
    schedule: cron || zone ? { cron: cron || null, zone: zone || null } : null,
    spacing: draft.spacing.trim() || null,
    anchor: draft.anchor || null,
  };
}

/** A served chore as the document an edit sends back — every field carried verbatim, its mode included. */
export function choreDocument(chore: ChoreView): ChoreDocument {
  return {
    key: chore.key,
    routine: chore.routine,
    scopes: chore.scopes,
    mode: chore.mode,
    promote: chore.promote,
    note: chore.note,
  };
}

/** The chore form's opening draft for a served chore. */
export function choreDraftOf(chore: ChoreView): ChoreDraft {
  return {
    key: chore.key,
    routine: chore.routine,
    scopes: chore.scopes === 'all' ? 'all' : (chore.scopes ?? []).join(', '),
    mode: chore.mode,
    promote: chore.promote,
    note: chore.note ?? '',
  };
}


/** The document a chore draft states. Scopes read `all`, a comma- or space-separated slug list, or blank for the
 * routine's default scope. */
export function draftDocument(draft: ChoreDraft): ChoreDocument {
  const scopes = draft.scopes.trim();
  return {
    key: draft.key.trim(),
    routine: draft.routine.trim(),
    scopes: scopes === 'all' ? 'all' : scopes === '' ? null : scopes.split(/[\s,]+/).filter((s) => s !== ''),
    mode: draft.mode as ChoreDocument['mode'],
    promote: draft.promote,
    note: draft.note.trim() || null,
  };
}

/** The whole chore list with `draft` added at its end. */
export function choresAdding(chores: readonly ChoreView[], draft: ChoreDraft): ChoreDocument[] {
  return [...chores.map(choreDocument), draftDocument(draft)];
}

/** The whole chore list with the chore keyed `key` replaced, in place, by `draft`. */
export function choresReplacing(chores: readonly ChoreView[], key: string, draft: ChoreDraft): ChoreDocument[] {
  return chores.map((chore) => (chore.key === key ? draftDocument(draft) : choreDocument(chore)));
}

/** The whole chore list without the chore keyed `key`. */
export function choresRemoving(chores: readonly ChoreView[], key: string): ChoreDocument[] {
  return chores.filter((chore) => chore.key !== key).map(choreDocument);
}
