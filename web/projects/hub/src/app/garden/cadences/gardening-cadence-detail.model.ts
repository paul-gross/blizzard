import { asyncState, type AsyncStateQuery, type CadenceErrandView, formatAbsolute } from 'fleet';

import { type Cadence, type CadenceAttention, type Chore, type ChoreDraft, vocabularyText } from '../core/cadence.domain';
import type { ChoresBaseline } from './chore.service';
import type { CadenceLifecycleVars } from './cadence-lifecycle.mutations';
import type { AttentionVm, CadencePanelVm, ChorePanelVm, ErrandRowVm } from './cadence-panel';
import { attentionReasonLabel, choreModeLabel, errandStateLook } from './cadence-vocabulary';
import { rhythmText } from './gardening-cadences-page.model';

/** The cadence `slug` names, or `null` on the bare child route or for a slug no loaded cadence holds. */
export function cadenceBySlug(slug: string | null, cadences: readonly Cadence[]): Cadence | null {
  return slug === null ? null : (cadences.find((c) => c.slug === slug) ?? null);
}

/** `cadence`'s `retired` flag as it reads once a pending retire or enable of it settles — `null` while none is. */
export function cadenceOverrideRetired(
  cadence: Cadence | null,
  pending: readonly CadenceLifecycleVars[],
): boolean | null {
  if (cadence === null) return null;
  const vars = pending.find((v) => v.cadenceId === cadence.id);
  return vars ? vars.retired : null;
}

/** One attention entry as the panel lists it: what it stops, and why. */
export function attentionVm(attention: CadenceAttention): AttentionVm {
  return { chore: attention.chore, scope: attention.scope, reason: attentionReasonLabel(vocabularyText(attention.reason)) };
}

/** Which scopes a chore runs over, as text: every linked scope, the listed ones, or the routine's default. */
export function choreScopesText(scopes: Chore['scopes']): string {
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

/** The cadence's chores, each with its own errands and read state. */
export function choreRows(cadence: Cadence, errands: readonly CadenceErrandView[] | undefined, query: AsyncStateQuery): readonly ChorePanelVm[] {
  return cadence.chores.map((chore) => ({
    key: chore.key,
    routine: chore.routine,
    scopes: choreScopesText(chore.scopes),
    mode: choreModeLabel(vocabularyText(chore.mode)),
    promote: chore.promote,
    note: chore.note,
    heldMode: vocabularyText(chore.mode),
    errands: (errands ?? []).filter((e) => e.chore === chore.key).map(errandRow),
    errandsState: asyncState(query, !(errands ?? []).some((e) => e.chore === chore.key)),
  }));
}

/** The cadence panel's view model — `null` with no cadence selected. */
export function cadencePanelVm(
  cadence: Cadence | null,
  overrideRetired: boolean | null,
  errands: readonly CadenceErrandView[] | undefined,
  query: AsyncStateQuery = { isPending: () => errands === undefined, isError: () => false },
): CadencePanelVm | null {
  if (cadence === null) return null;
  return {
    cadenceId: cadence.id,
    slug: cadence.slug,
    name: cadence.name,
    revision: cadence.revision,
    retired: cadence.retired,
    renderedRetired: overrideRetired ?? cadence.retired,
    rhythm: rhythmText(cadence),
    nextMoments: cadence.nextMoments.map((iso) => ({ iso, text: formatAbsolute(iso) })),
    attention: cadence.attention.map(attentionVm),
    chores: choreRows(cadence, errands, query),
  };
}

/** The edit's field tree while an edit is under way — while it holds a baseline — else `null`. */
export function whileEditing<T>(baseline: unknown, tree: T): T | null {
  return baseline === null ? null : tree;
}

/** One chore add or edit under way: what it started from, and the key of the chore it edits (`null` for an add). */
export interface ChoreSession {
  readonly baseline: ChoresBaseline;
  readonly key: string | null;
}

/** The chore form the panel renders for `session` — its add tree for an add, its edit tree for an edit — else `null`. */
export function choreFormVm<T>(
  session: ChoreSession | null,
  addTree: T,
  editTree: T,
): { readonly key: string | null; readonly form: T } | null {
  if (session === null) return null;
  return { key: session.key, form: session.key === null ? addTree : editTree };
}

/** The chore keyed `key` in `baseline`, `null` when it holds none. */
export function baselineChore(baseline: ChoresBaseline, key: string): Chore | null {
  return baseline.draft.find((chore) => chore.key === key) ?? null;
}

/** What a refused chore removal says on the panel line: the form line, else the first field's message, else `fallback`. */
export function removalRefusal(outcome: { form: string | null; fields: Partial<Record<keyof ChoreDraft, string>> }, fallback: string): string {
  return outcome.form ?? Object.values(outcome.fields)[0] ?? fallback;
}
