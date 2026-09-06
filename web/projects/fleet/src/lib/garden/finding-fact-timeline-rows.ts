import type { FindingFactView } from '../api/hub';
import { formatAbsolute, formatWhen } from '../when';

/** One entry in a finding's fact chain (blizzard#487), re-read for display — the
 * chain itself carries no id per entry, so {@link deriveFactTimelineRows} keys each
 * row off its own position in the (already oldest-first, never reordered within one
 * render) array rather than inventing one. {@link whenTitle} is the full-datetime
 * tooltip text beside {@link when}'s short form, `chunk-timeline-rows.ts`'s own
 * `HistoryRow` shape. */
export interface FindingFactRow {
  readonly key: string;
  readonly kind: string;
  readonly label: string;
  readonly note: string | null;
  readonly actor: string | null;
  readonly when: string;
  readonly whenTitle: string;
  readonly proposalId: string | null;
  readonly supersededBy: string | null;
}

/** A short human label per fact kind — `add`/`observed` never carry a note (a
 * routine sweep recording or re-confirming a finding), `gone` and the
 * human-driven exit/reopen verbs do. */
export const FACT_KIND_LABELS: Record<string, string> = {
  add: 'Added',
  observed: 'Observed',
  gone: 'Gone',
  resolved: 'Resolved',
  'gone-confirmed': 'Confirmed gone',
  'wont-fix': "Won't fix",
  'not-a-finding': 'Not a finding',
  superseded: 'Superseded',
  reopened: 'Reopened',
};

/**
 * A finding's whole fact chain (blizzard#487), re-read as timeline rows — a pure map
 * over `facts` in the array's own order (already oldest-first off the wire; never
 * re-sorted here). The single owner of this derivation (`canon:one-owner`,
 * `chunk-timeline-rows.ts`'s own precedent) — {@link FleetFindingFactTimeline} reads
 * it rather than re-deriving it inline.
 */
export function deriveFactTimelineRows(facts: readonly FindingFactView[]): readonly FindingFactRow[] {
  return facts.map((fact, index) => ({
    key: `${index}`,
    kind: fact.kind,
    label: FACT_KIND_LABELS[fact.kind] ?? fact.kind,
    note: fact.note ?? null,
    actor: fact.actor ?? null,
    when: formatWhen(fact.recorded_at),
    whenTitle: formatAbsolute(fact.recorded_at),
    proposalId: fact.proposal_id ?? null,
    supersededBy: fact.superseded_by ?? null,
  }));
}
