// exemplar: bzh:frontend-domain-boundary, bzh:frontend-edit-baseline, bzh:frontend-mutation-settles-on-refresh.

/** An anchor this build does not know, carried as the hub sent it so it renders as itself. */
export interface UnlistedAnchor {
  readonly unlisted: string;
}

/** What a cadence's spacing counts from. */
export type CadenceAnchor = 'scheduled' | 'started' | 'finished';

/** The anchors a spacing may count from, in the order a form offers them. */
export const CADENCE_ANCHORS: readonly CadenceAnchor[] = ['scheduled', 'started', 'finished'];

/** A cadence's cron and the zone it reads in. */
export interface CadenceSchedule {
  readonly cron: string;
  readonly zone: string;
}

/** A cadence as the hub reads it, its iteration included. */
export interface Cadence {
  readonly id: string;
  readonly revision: number;
  readonly slug: string;
  readonly name: string;
  readonly schedule: CadenceSchedule | null;
  readonly spacingMinutes: number | null;
  /** What the spacing counts from; `null` when unstated. */
  readonly anchor: CadenceAnchor | UnlistedAnchor | null;
}

/** A cadence as its edit form states it, every field as typed. A blank field is left unstated. */
export interface CadenceDraft {
  readonly name: string;
  readonly cron: string;
  readonly zone: string;
  /** As typed: a whole number and `m`, `h`, or `d` — `10d`. */
  readonly spacing: string;
  readonly anchor: CadenceAnchor | 'unstated';
}

/** An empty cadence draft, held by the form while no edit is under way. */
export const EMPTY_CADENCE_DRAFT: CadenceDraft = { name: '', cron: '', zone: '', spacing: '', anchor: 'unstated' };
