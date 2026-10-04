import { type GardenProposalView, type KitChipOption } from 'fleet';
import { type GardenProposalAcceptVars, type GardenProposalPassVars } from './garden-proposal.mutations';
import { type ProposalListRowVm } from './proposal-list';

/** A proposal is still waiting on a person exactly when it carries no closure
 * (`GardenProposalView.closure`) — the gardening strip's own reading of
 * "waiting", shared here so the tab shell and any future docket sheet agree. */
export function isGardenProposalWaiting(proposal: GardenProposalView): boolean {
  return proposal.closure == null;
}

/** The class chip row's "All classes" value. */
export const ALL_CLASSES = 'all';

/** Every real class chip's value carries this prefix, so it can never collide with
 * {@link ALL_CLASSES} no matter what a deployment names a class (`class` is opaque,
 * deployment-chosen vocabulary) — a class literally named `all` is a
 * real possibility, not a contrived one, and `KitChips` tracks and selects by
 * `value` alone. The class chip's `testid` carries its own `-item-` guard against the
 * same collision, one prefix protecting each of the two identifiers `KitChips` reads
 * off an option. */
export const CLASS_VALUE_PREFIX = 'class:';

/** The docket's waiting filter as it rides the URL: absent is the default (only
 * proposals not yet closed), and this one value widens it to every proposal. */
export const SHOW_ALL = 'all';

/** The routine chip row's "All routines" value. Unlike `class`, `routine_name` is
 * not opaque deployment vocabulary — it names one of blizzard's own gardening
 * routines — so it needs no `CLASS_VALUE_PREFIX`-style collision guard. */
export const ALL_ROUTINES = 'all';

/** The docket's three filters plus the proposals a Pass/Accept is in flight for. */
export interface ProposalFilters {
  readonly waitingOnly: boolean;
  /** `null` means every class. */
  readonly cls: string | null;
  /** `null` means every routine. */
  readonly routine: string | null;
  readonly closing: ReadonlySet<string>;
}

/** Every proposal id a Pass or Accept mutation is currently pending for
 * (`bzh:frontend-pending-override`) — closure is terminal whichever kind lands, so
 * only the id matters. */
export function pendingClosureIds(
  passes: readonly GardenProposalPassVars[],
  accepts: readonly GardenProposalAcceptVars[],
): ReadonlySet<string> {
  return new Set([...passes, ...accepts].map((v) => v.proposalId));
}

/** Every class present in the fetched data, alphabetized, each with an "All
 * classes" chip ahead of them — never a hardcoded vocabulary. */
export function proposalClassChips(proposals: readonly GardenProposalView[]): readonly KitChipOption[] {
  const classes = Array.from(new Set(proposals.map((p) => p.class))).sort((a, b) => a.localeCompare(b));
  return [
    { value: ALL_CLASSES, label: 'All classes', testid: 'gardening-proposal-class-all' },
    ...classes.map((c) => ({
      value: CLASS_VALUE_PREFIX + c,
      label: c,
      testid: `gardening-proposal-class-item-${c}`,
    })),
  ];
}

/** The class chip value naming `cls`, or the "All classes" chip for `null`. */
export function classChipValue(cls: string | null): string {
  return cls === null ? ALL_CLASSES : CLASS_VALUE_PREFIX + cls;
}

/** Every routine present in the fetched data, alphabetized, each with an "All
 * routines" chip ahead of them — never a hardcoded vocabulary, mirroring
 * {@link proposalClassChips}. A routine-less operator-authored proposal
 * contributes no chip of its own: `null` names no routine to filter by. */
export function proposalRoutineChips(proposals: readonly GardenProposalView[]): readonly KitChipOption[] {
  const names = proposals.map((p) => p.routine_name).filter((name) => name !== null);
  const routines = Array.from(new Set(names)).sort((a, b) => a.localeCompare(b));
  return [
    { value: ALL_ROUTINES, label: 'All routines', testid: 'gardening-proposal-routine-all' },
    ...routines.map((r) => ({
      value: r,
      label: r,
      testid: `gardening-proposal-routine-item-${r}`,
    })),
  ];
}

/** The waiting chip value for the current waiting filter. */
export function waitingChipValue(waitingOnly: boolean): string {
  return waitingOnly ? 'waiting' : SHOW_ALL;
}

/** The filtered set every other docket view model derives from: a passed proposal
 * leaves the waiting set and stays reachable under `all`. Under `waitingOnly`, a
 * proposal with a pending Pass/Accept drops out too (`bzh:frontend-pending-override`)
 * — purely computed off the mutations' own variables, never a cache write, so a
 * rejected call reverts the row for free the instant its `isPending()` clears. Only
 * matters in this branch: under `all` the proposal stays visible regardless of its
 * closure, pending or not. */
export function filterProposals(
  proposals: readonly GardenProposalView[],
  filters: ProposalFilters,
): readonly GardenProposalView[] {
  const { waitingOnly, cls, routine, closing } = filters;
  return proposals.filter(
    (p) =>
      (!waitingOnly || (isGardenProposalWaiting(p) && !closing.has(p.proposal_id))) &&
      (cls === null || p.class === cls) &&
      (routine === null || p.routine_name === routine),
  );
}

/** One docket row per proposal. */
export function proposalListRows(proposals: readonly GardenProposalView[]): readonly ProposalListRowVm[] {
  return proposals.map((p) => ({
    proposalId: p.proposal_id,
    title: p.title,
    proposalClass: p.class,
    waiting: isGardenProposalWaiting(p),
    createdAt: p.created_at,
  }));
}
