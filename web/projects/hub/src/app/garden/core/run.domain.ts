// bzh:frontend-domain-boundary — a routine run as its form states it; no wire, no Angular.

/** The modes a run offers, in the order the form lists them. */
export const RUN_MODES = ['full', 'delta'] as const;

/** A routine run as its form states it, every field as typed. */
export interface RunDraft {
  /** The chosen scope's slug; `''` until one is chosen. */
  readonly scope: string;
  /** `full`, or `delta` once the routine has a baseline for the scope. */
  readonly mode: string;
  /** An urgency slug; `''` is the routine's default. */
  readonly urgency: string;
  /** A note folded into the run's charge; blank is left unstated. */
  readonly note: string;
}

/** The run a form opens on: no scope yet, a full sweep at the routine's default urgency. */
export const EMPTY_RUN_DRAFT: RunDraft = { scope: '', mode: 'full', urgency: '', note: '' };
