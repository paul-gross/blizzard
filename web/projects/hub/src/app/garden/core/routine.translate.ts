// bzh:frontend-domain-boundary — the one place the routine wire is read and written.
import { type EditBaseline, type hubApi, type RefusalMap, type RoutineView, type UrgencyRef } from 'fleet';

import type { Routine, RoutineCreateDraft, RoutineEditDraft, RoutineKeeper, RoutineUrgency } from './routine.domain';

function keeperOf(wire: RoutineView['kept_by'][number]): RoutineKeeper {
  return {
    cadenceSlug: wire.cadence_slug,
    cadenceName: wire.cadence_name,
    choreKey: wire.chore_key,
    nextMoment: wire.next_moment,
  };
}

/** A served routine as the domain holds it. Fields an older hub omits read as their empty values. */
export function fromRoutineView(wire: RoutineView): Routine {
  return {
    id: wire.routine_id,
    revision: wire.revision ?? null,
    slug: wire.slug,
    name: wire.name,
    graph: wire.graph_name,
    defaultScope: wire.default_scope_slug,
    defaultUrgency: wire.default_urgency ?? null,
    effectiveUrgency: wire.effective_urgency
      ? { id: wire.effective_urgency.urgency_id, slug: wire.effective_urgency.slug }
      : null,
    defaultModel: wire.default_model ?? [],
    defaultEffort: wire.default_effort ?? null,
    defaultHarnesses: wire.default_harnesses ?? [],
    projectId: wire.project_id,
    projectName: wire.project_name,
    projectSlug: wire.project_slug,
    retired: wire.retired ?? false,
    keptBy: (wire.kept_by ?? []).map(keeperOf),
    createdAt: wire.created_at,
  };
}

/**
 * What an edit of `routine` starts from: its id, the revision it was read at, and its draft, from that one read.
 * `null` when the routine has no revision — nothing to guard an edit with, so none is offered.
 */
export function toEditBaseline(routine: Routine): EditBaseline<RoutineEditDraft> | null {
  if (routine.revision === null) return null;
  return {
    id: routine.id,
    revision: routine.revision,
    draft: { graph: routine.graph, urgency: routine.defaultUrgency ?? '' },
  };
}

/** An urgency as the run dialog's wire-shaped input takes it — until the run dialog moves behind the boundary. */
export function toUrgencyRef(urgency: RoutineUrgency | null): UrgencyRef | null {
  return urgency === null ? null : { urgency_id: urgency.id, slug: urgency.slug };
}

const CREATE_REFUSAL_FIELDS: Readonly<Record<string, keyof RoutineCreateDraft>> = {
  slug: 'slug',
  name: 'name',
  graph_name: 'graph',
  default_scope_slug: 'scope',
  default_urgency: 'urgency',
};

/**
 * How the hub's routine-create refusals reach the draft: each validation entry to its own field, and a malformed scope
 * slug to the scope. A refusal naming no field — a taken slug, an unresolved graph — lands on the form line.
 */
export const ROUTINE_CREATE_REFUSALS: RefusalMap<RoutineCreateDraft> = {
  field: (path) => (Object.hasOwn(CREATE_REFUSAL_FIELDS, path) ? CREATE_REFUSAL_FIELDS[path] : null),
  kinds: { 'urn:blizzard:problem:scope-slug-invalid': 'scope' },
};

/** The create body a draft states: the slug and default scope exactly as typed — the hub refuses a malformed slug, it is
 * never normalized here — the graph trimmed, a blank name stated as `null`, and a blank urgency as `null`, the tenant
 * default. */
export function toCreateRequest(draft: RoutineCreateDraft): hubApi.RoutineCreateRequest {
  return {
    slug: draft.slug,
    name: draft.name.trim() || null,
    graph_name: draft.graph.trim(),
    default_scope_slug: draft.scope,
    default_urgency: draft.urgency || null,
  };
}

const EDIT_REFUSAL_FIELDS: Readonly<Record<string, keyof RoutineEditDraft>> = {
  graph_name: 'graph',
  default_urgency: 'urgency',
};

/** The one field an edit from `before` to `after` changes, or `null` when it changes none or several. */
function changedField(before: RoutineEditDraft, after: RoutineEditDraft): keyof RoutineEditDraft | null {
  const graph = after.graph.trim() !== before.graph.trim();
  const urgency = after.urgency !== before.urgency;
  if (graph === urgency) return null;
  return graph ? 'graph' : 'urgency';
}

/**
 * How the hub's routine-edit refusals reach the draft: each validation entry to its own field. A definition the hub
 * cannot resolve names no field, so it lands on the one field the edit changed — the open one.
 */
export function routineEditRefusals(before: RoutineEditDraft, after: RoutineEditDraft): RefusalMap<RoutineEditDraft> {
  const changed = changedField(before, after);
  return {
    field: (path) => (Object.hasOwn(EDIT_REFUSAL_FIELDS, path) ? EDIT_REFUSAL_FIELDS[path] : null),
    kinds: changed === null ? {} : { 'urn:blizzard:problem:routine-definition-invalid': changed },
  };
}

/** The edit body `after` states against the draft it started from: only the fields that changed. A graph goes trimmed;
 * a blank urgency is the tenant default, `null`. */
export function toEditRequest(before: RoutineEditDraft, after: RoutineEditDraft): hubApi.RoutineEditRequest {
  const graph = after.graph.trim();
  return {
    ...(graph !== before.graph.trim() ? { graph_name: graph } : {}),
    ...(after.urgency !== before.urgency ? { default_urgency: after.urgency || null } : {}),
  };
}
