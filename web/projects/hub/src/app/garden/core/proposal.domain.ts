// bzh:frontend-domain-boundary — a garden proposal and its closure as the hub reads them; no wire, no Angular.
import type { Vocabulary } from './vocabulary.domain';

/** Who raised a proposal: a routine run, or an operator. */
export type ProposalOrigin = 'routine-run' | 'operator';

/** How a proposal closed: passed over, or accepted. */
export type ProposalClosureKind = 'passed' | 'accepted';

/** What an acceptance did about a work item: minted one, or declined to. */
export type ProposalItemOutcome = 'minted' | 'declined';

/** A proposal's closure record. `source`/`ref` point at the minted work item. */
export interface ProposalClosure {
  readonly kind: Vocabulary<ProposalClosureKind>;
  readonly closedBy: string;
  readonly closedAt: string;
  readonly reason: string | null;
  readonly itemOutcome: Vocabulary<ProposalItemOutcome> | null;
  readonly source: string | null;
  readonly ref: string | null;
}

/** A garden proposal as the hub reads it. */
export interface Proposal {
  readonly id: string;
  readonly title: string;
  readonly body: string;
  /** The proposal's class — opaque, deployment-chosen vocabulary. */
  readonly proposalClass: string;
  readonly origin: Vocabulary<ProposalOrigin>;
  readonly createdBy: string | null;
  readonly createdAt: string;
  /** The slug of the routine the proposal names; `null` when it names none. */
  readonly routineSlug: string | null;
  readonly projectId: string | null;
  /** The ids of the findings the proposal cites. */
  readonly findingIds: readonly string[];
  /** `null` while the proposal is still waiting on a person. */
  readonly closure: ProposalClosure | null;
}
