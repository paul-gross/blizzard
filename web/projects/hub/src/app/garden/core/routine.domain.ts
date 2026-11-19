// bzh:frontend-domain-boundary — the routine as the hub reads it; no wire, no Angular.

/** An urgency as a routine names it: its id and the slug it reads as. */
export interface RoutineUrgency {
  readonly id: string;
  readonly slug: string;
}

/** One cadence chore keeping a routine, with the moment it is next due. */
export interface RoutineKeeper {
  readonly cadenceSlug: string;
  readonly cadenceName: string;
  readonly choreKey: string;
  readonly nextMoment: string | null;
}

/** A routine as the hub reads it. */
export interface Routine {
  readonly id: string;
  /** The version the read was taken at; `null` when the hub serves none, which offers no edit. */
  readonly revision: number | null;
  readonly slug: string;
  readonly name: string;
  readonly graph: string;
  readonly defaultScope: string;
  /** The urgency id the routine states, or `null` for the tenant default. */
  readonly defaultUrgency: string | null;
  /** The urgency the routine runs at once the tenant default is applied, when the hub says. */
  readonly effectiveUrgency: RoutineUrgency | null;
  readonly defaultModel: readonly string[];
  readonly defaultEffort: string | null;
  readonly defaultHarnesses: readonly string[];
  readonly projectId: string | null;
  readonly projectName: string | null;
  readonly projectSlug: string | null;
  readonly retired: boolean;
  readonly keptBy: readonly RoutineKeeper[];
  readonly createdAt: string;
}

/** The fields a routine's inline edits state, as typed: the graph's name, and the urgency id (`''` is the tenant default). */
export interface RoutineEditDraft {
  readonly graph: string;
  readonly urgency: string;
}

/** The fields a routine create states, as typed: `urgency` is an urgency slug, `''` for the tenant default. */
export interface RoutineCreateDraft {
  readonly slug: string;
  readonly name: string;
  readonly graph: string;
  readonly scope: string;
  readonly urgency: string;
}

export const EMPTY_ROUTINE_CREATE_DRAFT: RoutineCreateDraft = { slug: '', name: '', graph: '', scope: '', urgency: '' };
