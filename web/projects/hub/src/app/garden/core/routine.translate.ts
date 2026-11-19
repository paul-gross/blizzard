// bzh:frontend-domain-boundary — the one place the routine wire is read and written.
import { type EditBaseline, type RoutineView, type UrgencyRef } from 'fleet';

import type { Routine, RoutineEditDraft, RoutineKeeper, RoutineUrgency } from './routine.domain';

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
