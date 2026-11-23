/** Shapes recognized in structured artifact bodies, independent of the transport. */
export interface FindingCandidate {
  class: string;
  introduced: string | null;
  locus: string;
  ref: string | null;
  summary: string;
}

export type AddFindingOp = FindingCandidate & { op: 'add' };
export interface ObservedFindingOp { op: 'observed'; id: string }
export interface GoneFindingOp { op: 'gone'; id: string; note: string }

export interface FindingDelta {
  scope: string;
  revisions: Record<string, string>;
  measurement: string | null;
  findings: (AddFindingOp | ObservedFindingOp | GoneFindingOp)[];
}

export interface FindingSurvey {
  scope: string;
  revisions: Record<string, string>;
  measurement: string | null;
  candidates: FindingCandidate[];
}

export interface BounceAction { name: string; choice: string; log_artifact: string | null }
export interface BounceUnlandedRepo {
  repo: string;
  branch: string;
  commit: string;
  pr: { number: number; url: string } | null;
}
export interface ParsedBounceEnvelope {
  cause: string;
  detail: string;
  unlanded: BounceUnlandedRepo[];
  action: BounceAction | null;
  /** Absent in older envelopes, null when known to have no diagnostic. */
  diagnostic: string | null | undefined;
}
